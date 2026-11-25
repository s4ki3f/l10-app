"""Real-time path end to end, over real HTTP: commit → NOTIFY → LISTEN → SSE → the other user's stream.

A real uvicorn server runs in a thread against the test database with real commits; two users each
hold their own connection. Brief: "changes reach all attendees within about a second".
"""
import json
import queue
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.db import models as m
from app.main import app
from app.realtime import broker
from app.rules.clock import FrozenClock

from .conftest import NOW

WITHIN = 1.0  # seconds


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(engine):
    factory = sessionmaker(engine, expire_on_commit=False)

    def per_request():
        with factory() as s:
            yield s

    app.dependency_overrides[get_db] = per_request
    original = app.state.clock
    app.state.clock = FrozenClock(NOW)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)
    app.state.clock = original
    app.dependency_overrides.clear()
    with engine.begin() as conn:
        conn.execute(delete(m.Meeting).where(m.Meeting.id.not_in(["mt1", "mt2"])))
        conn.execute(delete(m.Todo).where(m.Todo.id.like("t\\_%")))
        conn.execute(delete(m.Issue).where(m.Issue.id.like("i\\_%")))


def signed_in(base: str, user: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=5)
    assert c.post("/auth/login", json={"user_id": user}).status_code == 200
    return c


class Stream:
    """Reads one SSE stream in a background thread; events land on a queue."""

    def __init__(self, client: httpx.Client, team: str):
        self.events: queue.Queue = queue.Queue()
        self.status: int | None = None
        self._stop = threading.Event()
        self._opened = threading.Event()
        threading.Thread(target=self._run, args=(client, team), daemon=True).start()
        self._opened.wait(5)

    def _run(self, client, team):
        with client.stream("GET", f"/teams/{team}/events", timeout=None) as r:
            self.status = r.status_code
            self._opened.set()
            if r.status_code != 200:
                return
            event = None
            for line in r.iter_lines():
                if self._stop.is_set():
                    return
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    self.events.put((event, json.loads(line[6:])))

    def next(self, timeout=WITHIN):
        return self.events.get(timeout=timeout)

    def expect(self, topic, timeout=WITHIN):
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            event, data = self.next(left)
            if event == "change" and data.get("topic") == topic:
                return data
        raise AssertionError(f"no {topic!r} event within {timeout}s")

    def close(self):
        self._stop.set()


def test_change_reaches_the_other_browser_within_a_second(server):
    broker.ensure_started()
    assert broker.ready.wait(5), "LISTEN connection never came up"
    watcher, actor = signed_in(server, "u2"), signed_in(server, "u3")
    stream = Stream(watcher, "team_lead")
    try:
        assert stream.status == 200
        assert stream.next(2)[0] == "hello"

        t0 = time.monotonic()
        assert actor.post("/measurables/m3/weeks/2026-11-02/issue").status_code == 201
        stream.expect("issues")
        assert time.monotonic() - t0 < WITHIN

        mt = actor.post("/teams/team_lead/meetings").json()["id"]
        stream.expect("meeting")
        assert watcher.post(f"/meetings/{mt}/join").status_code == 200
        stream.expect("meeting")

        t0 = time.monotonic()
        actor.post(f"/meetings/{mt}/advance", json={"from_section": "scorecard"})
        stream.expect("meeting")
        assert time.monotonic() - t0 < WITHIN
        assert watcher.get(f"/meetings/{mt}").json()["section"] == "ids"  # the refetch the browser does
    finally:
        stream.close()


def test_other_teams_never_hear_it(server):
    broker.ensure_started()
    broker.ready.wait(5)
    ops = Stream(signed_in(server, "u8"), "team_ops")
    try:
        assert ops.next(2)[0] == "hello"
        signed_in(server, "u2").post("/teams/team_lead/issues", json={"title": "lead-only news"})
        with pytest.raises(queue.Empty):
            ops.expect("issues", timeout=0.7)
    finally:
        ops.close()


def test_outsider_cannot_open_a_stream(server):
    s = Stream(signed_in(server, "u6"), "team_lead")
    assert s.status == 404


def test_anonymous_cannot_open_a_stream(server):
    with httpx.Client(base_url=server) as c:
        assert c.get("/teams/team_lead/events").status_code == 401
