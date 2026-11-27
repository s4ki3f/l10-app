"""Real-time fan-out: Postgres NOTIFY → every API process → its SSE clients.

- `publish(db, team_id, topic)` runs `pg_notify` inside the caller's transaction, so a message is
  delivered only if (and when) the change commits. Rolled-back work never notifies.
- `broker` holds one LISTEN connection per process, in a daemon thread, and hands each message to
  every subscriber on that subscriber's own event loop (so it works under uvicorn and any test client).
- Messages say only *what* changed ({"topic": "issues"}); browsers refetch through the guarded API,
  so permissions are applied in one place and a missed message costs nothing.
"""
import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

import psycopg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .settings import database_url

CHANNEL = "l10_events"
TOPICS = ("scorecard", "issues", "todos", "meeting")
RESYNC = {"topic": "resync"}  # "you may have missed hints: refetch everything"
log = logging.getLogger("l10.realtime")


def publish(db: Session, team_id: str, topic: str, **extra) -> None:
    assert topic in TOPICS, topic
    db.execute(text("SELECT pg_notify(:channel, :payload)"),
               {"channel": CHANNEL, "payload": json.dumps({"team_id": team_id, "topic": topic, **extra})})


def _libpq_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@dataclass(eq=False)
class Subscription:
    team_id: str
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    overflowed: bool = False  # set when hints were dropped; the stream then sends a resync


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()

    # ---- subscribers (called on an event loop) ----
    def subscribe(self, team_id: str) -> Subscription:
        self.ensure_started()
        sub = Subscription(team_id, asyncio.Queue(maxsize=100), asyncio.get_running_loop())
        with self._lock:
            self._subs[team_id].add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs[sub.team_id].discard(sub)

    # ---- delivery (called from the listener thread) ----
    def dispatch(self, message: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(message.get("team_id"), ()))
        self._deliver(subs, message)

    def resync_all(self) -> None:
        """After the LISTEN connection was lost, hints in the gap are gone: tell every stream to refetch."""
        with self._lock:
            subs = [s for team in self._subs.values() for s in team]
        self._deliver(subs, RESYNC)

    def _deliver(self, subs: list[Subscription], message: dict) -> None:
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(_offer, sub, message)
            except RuntimeError:  # that loop is closed; the subscriber is gone
                self.unsubscribe(sub)

    # ---- the LISTEN connection ----
    def ensure_started(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._listen, name="l10-listen", daemon=True)
                self._thread.start()

    def _listen(self) -> None:
        backoff, was_lost = 0.5, False
        while True:
            try:
                with psycopg.connect(_libpq_url(database_url()), autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.ready.set()
                    backoff = 0.5
                    if was_lost:  # NOTIFY isn't queued for absent listeners: anything in the gap was lost
                        self.resync_all()
                        was_lost = False
                    for note in conn.notifies():
                        try:
                            self.dispatch(json.loads(note.payload))
                        except ValueError:
                            log.warning("bad payload on %s: %r", CHANNEL, note.payload)
            except Exception as exc:  # database restart or network blip: reconnect, then resync everyone
                self.ready.clear()
                was_lost = True
                log.warning("LISTEN connection lost (%s); retrying in %.1fs", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 10)


def _offer(sub: Subscription, message: dict) -> None:
    try:
        sub.queue.put_nowait(message)
    except asyncio.QueueFull:  # a stalled client: drop, and make the stream send a resync when it catches up
        sub.overflowed = True


broker = Broker()
