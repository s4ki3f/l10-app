"""The Level 10 Meeting (brief 2.5 Meeting Page, 2.6 Meeting rating, 2.3 core journey)."""
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.db import models as m
from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW


def start(c, team="team_lead"):
    return c.post(f"/teams/{team}/meetings")


def code(r):
    return r.json()["error"]["code"]


@pytest.fixture
def live(as_user):
    """u3 starts a Leadership meeting; returns its id."""
    r = start(as_user("u3"))
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestStartAndJoin:
    def test_starter_becomes_facilitator_and_attendee(self, as_user):
        body = start(as_user("u3")).json()
        assert body["facilitator"]["id"] == "u3"
        assert [a["id"] for a in body["attendees"]] == ["u3"]
        assert (body["section"], body["elapsed_seconds"], body["over_time"]) == ("scorecard", 0, False)
        assert [(a["key"], a["minutes"]) for a in body["agenda"]] == [("scorecard", 5), ("ids", 60), ("conclude", 10)]

    def test_second_start_joins_the_live_meeting(self, as_user, live):
        r = start(as_user("u2"))
        assert (r.status_code, r.json()["id"], r.json()["facilitator"]["id"]) == (200, live, "u3")
        assert {a["id"] for a in r.json()["attendees"]} == {"u2", "u3"}

    def test_observer_cannot_start(self, as_user):
        assert start(as_user("u5")).status_code == 403

    def test_outsider_cannot_start_or_see(self, as_user, live):
        c = as_user("u6")
        assert start(c).status_code == 404
        assert c.get(f"/meetings/{live}").status_code == 404
        assert c.post(f"/meetings/{live}/join").status_code == 404

    def test_observer_can_join_and_watch(self, as_user, live):
        r = as_user("u5").post(f"/meetings/{live}/join")
        assert r.status_code == 200 and "u5" in {a["id"] for a in r.json()["attendees"]}

    def test_join_twice_is_one_attendance(self, as_user, live):
        c = as_user("u2")
        c.post(f"/meetings/{live}/join")
        assert [a["id"] for a in c.post(f"/meetings/{live}/join").json()["attendees"]].count("u2") == 1

    def test_active_meeting_is_what_a_late_joiner_or_refresh_loads(self, as_user, live):
        body = as_user("u4").get("/teams/team_lead/meetings/active").json()
        assert body["meeting"]["id"] == live and body["server_now"].startswith("2026-11-11T15:00:00")

    def test_no_active_meeting_is_null(self, as_user):
        assert as_user("u8").get("/teams/team_ops/meetings/active").json()["meeting"] is None


class TestControl:
    def test_facilitator_advances_through_the_agenda(self, as_user, live):
        c = as_user("u3")
        assert c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"}).json()["section"] == "ids"
        assert c.post(f"/meetings/{live}/advance", json={"from_section": "ids"}).json()["section"] == "conclude"
        r = c.post(f"/meetings/{live}/advance", json={"from_section": "conclude"})
        assert (r.status_code, code(r)) == (409, "last_section")

    def test_only_the_facilitator_advances_or_ends(self, as_user, live):
        for user in ("u2", "u1"):  # a teammate, and even an Admin
            c = as_user(user)
            c.post(f"/meetings/{live}/join")
            assert c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"}).status_code == 403
            assert c.post(f"/meetings/{live}/end").status_code == 403

    def test_observer_cannot_advance(self, as_user, live):
        assert as_user("u5").post(f"/meetings/{live}/advance", json={"from_section": "scorecard"}).status_code == 403

    def test_stale_double_click_does_not_skip_a_section(self, as_user, live):
        c = as_user("u3")
        c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"})
        r = c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"})  # second click, stale view
        assert (r.status_code, code(r)) == (409, "stale_section")
        assert c.get(f"/meetings/{live}").json()["section"] == "ids"

    def test_facilitator_can_end_at_any_time(self, as_user, live):
        body = as_user("u3").post(f"/meetings/{live}/end").json()
        assert body["ended_at"] is not None
        assert as_user("u2").get("/teams/team_lead/meetings/active").json()["meeting"] is None

    def test_ended_meeting_rejects_commands_and_a_new_one_can_start(self, as_user, live):
        c = as_user("u3")
        c.post(f"/meetings/{live}/end")
        assert code(c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"})) == "meeting_ended"
        assert start(c).status_code == 201


class TestTimer:
    def test_overrun_shows_over_time_and_does_not_advance(self, as_user, live):
        c = as_user("u3")
        app.state.clock = FrozenClock(NOW + timedelta(minutes=7))  # Scorecard is 5 min
        body = c.get(f"/meetings/{live}").json()
        assert (body["section"], body["elapsed_seconds"], body["over_time"]) == ("scorecard", 420, True)

    def test_timer_restarts_per_section_from_server_time(self, as_user, live):
        c = as_user("u3")
        app.state.clock = FrozenClock(NOW + timedelta(minutes=3))
        c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"})
        app.state.clock = FrozenClock(NOW + timedelta(minutes=4))
        body = c.get(f"/meetings/{live}").json()
        assert (body["section"], body["elapsed_seconds"], body["section_limit_seconds"]) == ("ids", 60, 3600)

    def test_exactly_at_the_limit_is_not_over_time(self, as_user, live):
        app.state.clock = FrozenClock(NOW + timedelta(minutes=5))
        assert as_user("u3").get(f"/meetings/{live}").json()["over_time"] is False


class TestRating:
    def to_conclude(self, as_user, live):
        c = as_user("u3")
        c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"})
        c.post(f"/meetings/{live}/advance", json={"from_section": "ids"})

    def test_rating_only_in_conclude(self, as_user, live):
        r = as_user("u3").put(f"/meetings/{live}/rating", json={"rating": 8})
        assert (r.status_code, code(r)) == (409, "not_conclude")

    def test_attendees_rate_and_can_change_it(self, as_user, live):
        self.to_conclude(as_user, live)
        c = as_user("u2")
        c.post(f"/meetings/{live}/join")
        c.put(f"/meetings/{live}/rating", json={"rating": 6})
        as_user("u3").put(f"/meetings/{live}/rating", json={"rating": 9})
        body = as_user("u2").put(f"/meetings/{live}/rating", json={"rating": 8}).json()  # changed 6 → 8
        assert sorted((r["user_id"], r["rating"]) for r in body["ratings"]) == [("u2", 8), ("u3", 9)]
        assert body["average_rating"] == 8.5

    def test_must_attend_to_rate(self, as_user, live):
        self.to_conclude(as_user, live)
        r = as_user("u4").put(f"/meetings/{live}/rating", json={"rating": 7})
        assert (r.status_code, code(r)) == (409, "not_attending")

    def test_observer_cannot_rate(self, as_user, live):
        self.to_conclude(as_user, live)
        c = as_user("u5")
        c.post(f"/meetings/{live}/join")
        assert c.put(f"/meetings/{live}/rating", json={"rating": 7}).status_code == 403

    @pytest.mark.parametrize("rating", [0, 11, 7.5])
    def test_rating_must_be_1_to_10(self, as_user, live, rating):
        self.to_conclude(as_user, live)
        assert as_user("u3").put(f"/meetings/{live}/rating", json={"rating": rating}).status_code == 422


class TestSummary:
    def test_seeded_meeting_summary(self, as_user):
        body = as_user("u5").get("/meetings/mt2/summary").json()
        assert body["scorecard_week"] == "2026-10-26"
        assert {x["id"]: x["status"] for x in body["measurable_status"]} == {
            "m1": "off_track", "m2": "on_track", "m3": "on_track", "m4": "off_track", "m5": "on_track"}
        assert [x["id"] for x in body["issues_solved"]] == ["i6"]
        assert [x["id"] for x in body["todos_created"]] == ["t30"]
        assert [a["id"] for a in body["attendees"]] == ["u1", "u2", "u3", "u4", "u5"]
        assert body["rating"] == 8.3

    def test_live_meeting_summary_is_409(self, as_user, live):
        assert code(as_user("u3").get(f"/meetings/{live}/summary")) == "meeting_live"

    def test_summary_of_a_meeting_run_through_the_api(self, as_user, live):
        c = as_user("u3")
        c.post(f"/meetings/{live}/advance", json={"from_section": "scorecard"})
        todo = c.post("/issues/i1/todos", json={"title": "Draft VP job ad", "owner_id": "u3"}).json()
        c.post(f"/meetings/{live}/advance", json={"from_section": "ids"})
        c.put(f"/meetings/{live}/rating", json={"rating": 9})
        c.post(f"/meetings/{live}/end")
        body = c.get(f"/meetings/{live}/summary").json()
        assert body["scorecard_week"] == "2026-11-02"
        assert [t["id"] for t in body["todos_created"]] == [todo["id"]]
        assert body["rating"] == 9.0

    def test_other_team_summary_is_404(self, as_user):
        assert as_user("u8").get("/meetings/mt1/summary").status_code == 404


# ---------- two simultaneous Starts → one meeting ----------

@pytest.fixture
def real_db(committed_engine):
    factory = sessionmaker(committed_engine, expire_on_commit=False)

    def per_request():
        with factory() as s:
            yield s

    app.dependency_overrides[get_db] = per_request
    original = app.state.clock
    app.state.clock = FrozenClock(NOW)
    yield committed_engine
    app.state.clock = original
    app.dependency_overrides.clear()
    with committed_engine.begin() as conn:
        conn.execute(delete(m.Meeting).where(m.Meeting.id.not_in(["mt1", "mt2"])))


def test_simultaneous_starts_create_one_meeting(real_db):
    users = ["u1", "u2", "u3", "u4"] * 2
    barrier, out, lock = threading.Barrier(len(users)), [], threading.Lock()

    def go(u):
        with TestClient(app) as c:
            c.post("/auth/login", json={"user_id": u})
            barrier.wait()
            r = start(c)
            with lock:
                out.append((r.status_code, r.json()["id"]))

    ts = [threading.Thread(target=go, args=(u,)) for u in users]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(s for s, _ in out).count(201) == 1 and len({i for _, i in out}) == 1, out
    with real_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM meetings WHERE team_id='team_lead' AND ended_at IS NULL")).scalar() == 1


def test_rate_racing_end_never_lands_after_it(real_db):
    """End holds the meeting row while it commits; a Rate arriving meanwhile must wait, then see the End (409)."""
    with TestClient(app) as c:
        c.post("/auth/login", json={"user_id": "u3"})
        mt = start(c).json()["id"]
        c.post(f"/meetings/{mt}/advance", json={"from_section": "scorecard"})
        c.post(f"/meetings/{mt}/advance", json={"from_section": "ids"})

        result = {}
        with real_db.connect() as end_tx:  # plays the facilitator's End, mid-transaction
            tx = end_tx.begin()
            end_tx.execute(text("SELECT 1 FROM meetings WHERE id = :id FOR UPDATE"), {"id": mt})
            t = threading.Thread(target=lambda: result.update(r=c.put(f"/meetings/{mt}/rating", json={"rating": 9})))
            t.start()
            t.join(0.5)
            assert t.is_alive(), "Rate didn't wait for the End holding the meeting row"
            end_tx.execute(text("UPDATE meetings SET ended_at = started_at + interval '1 minute' WHERE id = :id"), {"id": mt})
            tx.commit()
        t.join(10)
        assert (result["r"].status_code, result["r"].json()["error"]["code"]) == (409, "meeting_ended")
        with real_db.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM meeting_ratings WHERE meeting_id = :id"), {"id": mt}).scalar() == 0
