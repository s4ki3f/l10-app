"""Make it an Issue (brief 2.5, 2.6 'No duplicate Issues').

Cells used (seed, APP_NOW = 2026-11-11):
- m1 / Oct 26: 9 vs at_least 10 → off track, no Issue          → create
- m1 / Nov 2:  off track, i5 is its unsolved Issue              → return i5
- m4 / Oct 26: off track, i6 solved                             → a new Issue is allowed
- m1 / Oct 19: 12 → on track                                    → 409
- m1 / Nov 9:  current week, no score → pending                 → 409
"""
import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.db import models as m
from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW

URL = "/measurables/{m}/weeks/{w}/issue"


def open_issues(session, measurable, week):
    return session.scalar(select(func.count()).select_from(m.Issue).where(
        m.Issue.source_measurable_id == measurable, m.Issue.source_week == date.fromisoformat(week),
        m.Issue.solved_at.is_(None)))


class TestMakeItAnIssue:
    def test_creates_issue_titled_with_measurable_and_week_owned_by_clicker(self, as_user):
        r = as_user("u3").post(URL.format(m="m1", w="2026-10-26"))
        body = r.json()
        assert r.status_code == 201
        assert body["title"] == "New deals closed, week of Oct 26"
        assert body["owner"]["id"] == "u3"
        assert body["source"] == {"measurable_id": "m1", "measurable_name": "New deals closed", "week_start": "2026-10-26"}
        assert body["priority"] is None
        assert body["created_at"].startswith("2026-11-11T15:00:00")

    def test_second_click_returns_the_same_issue_with_200(self, as_user, session):
        first = as_user("u3").post(URL.format(m="m1", w="2026-10-26")).json()
        r = as_user("u2").post(URL.format(m="m1", w="2026-10-26"))
        assert (r.status_code, r.json()["id"], r.json()["owner"]["id"]) == (200, first["id"], "u3")
        assert open_issues(session, "m1", "2026-10-26") == 1

    def test_existing_seeded_unsolved_issue_is_returned(self, as_user):
        r = as_user("u2").post(URL.format(m="m1", w="2026-11-02"))
        assert (r.status_code, r.json()["id"]) == (200, "i5")

    def test_solved_issue_does_not_block_a_new_one(self, as_user):
        r = as_user("u3").post(URL.format(m="m4", w="2026-10-26"))
        assert r.status_code == 201 and r.json()["id"] != "i6"

    def test_seed_title_format_matches(self, as_user):
        # the seed's own title for this kind of Issue
        assert as_user("u2").post(URL.format(m="m1", w="2026-11-02")).json()["title"] == "New deals closed, week of Nov 2"

    @pytest.mark.parametrize("measurable,week", [("m1", "2026-10-19"), ("m1", "2026-11-09")])
    def test_on_track_or_pending_cell_is_409(self, as_user, session, measurable, week):
        before = session.scalar(select(func.count()).select_from(m.Issue))
        r = as_user("u2").post(URL.format(m=measurable, w=week))
        assert (r.status_code, r.json()["error"]["code"]) == (409, "not_off_track")
        assert session.scalar(select(func.count()).select_from(m.Issue)) == before

    def test_closed_week_without_score_is_off_track(self, as_user):
        # m5 has no score for Nov 2 → off track → can be made an Issue
        assert as_user("u4").post(URL.format(m="m5", w="2026-11-02")).status_code == 201

    @pytest.mark.parametrize("week", ["2026-11-03", "2026-08-10", "2026-11-16", "not-a-date"])
    def test_week_must_be_a_monday_inside_the_scorecard(self, as_user, week):
        assert as_user("u2").post(URL.format(m="m1", w=week)).status_code == 422

    def test_observer_is_403(self, as_user, session):
        r = as_user("u5").post(URL.format(m="m1", w="2026-10-26"))
        assert r.status_code == 403
        assert open_issues(session, "m1", "2026-10-26") == 0

    def test_other_team_is_404(self, as_user):
        assert as_user("u6").post(URL.format(m="m1", w="2026-10-26")).status_code == 404

    def test_unknown_measurable_is_404(self, as_user):
        assert as_user("u2").post(URL.format(m="m999", w="2026-10-26")).status_code == 404


# ---------- brief minimum: concurrent Make it an Issue requests return one Issue ----------

@pytest.fixture
def real_sessions(committed_engine):
    """Each request gets its own session and connection, and commits for real (no shared transaction)."""
    factory = sessionmaker(committed_engine, expire_on_commit=False)

    def per_request():
        with factory() as s:
            yield s

    app.dependency_overrides[get_db] = per_request
    original_clock = app.state.clock
    app.state.clock = FrozenClock(NOW)
    created_before = {i for i in committed_engine.connect().execute(text("SELECT id FROM issues")).scalars()}
    yield committed_engine
    app.state.clock = original_clock
    app.dependency_overrides.clear()
    with committed_engine.begin() as conn:  # clean up what the race committed
        conn.execute(delete(m.Issue).where(m.Issue.id.not_in(created_before)))


@pytest.mark.parametrize("cell", [("m1", "2026-10-26"), ("m3", "2026-11-02")])
def test_concurrent_requests_return_one_issue(real_sessions, cell):
    measurable, week = cell
    users = ["u1", "u2", "u3", "u4"] * 3  # 12 concurrent clicks from four people (u1 is the admin, in Leadership)
    barrier = threading.Barrier(len(users))
    results: list[tuple[int, str, str]] = []
    lock = threading.Lock()

    def click(user_id):
        with TestClient(app) as c:
            assert c.post("/auth/login", json={"user_id": user_id}).status_code == 200
            barrier.wait()  # everyone fires at the same instant
            r = c.post(URL.format(m=measurable, w=week))
            with lock:
                results.append((r.status_code, r.json().get("id"), r.text))

    threads = [threading.Thread(target=click, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    statuses = sorted(s for s, _, _ in results)
    ids = {i for _, i, _ in results}
    assert len(results) == len(users), results
    assert statuses.count(201) == 1, statuses
    assert statuses.count(200) == len(users) - 1, [t for s, _, t in results if s not in (200, 201)]
    assert len(ids) == 1
    with real_sessions.connect() as conn:
        count = conn.execute(text(
            "SELECT count(*) FROM issues WHERE source_measurable_id = :m AND source_week = :w AND solved_at IS NULL"
        ), {"m": measurable, "w": week}).scalar()
    assert count == 1
