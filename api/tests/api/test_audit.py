"""Bonus: the audit trail — who changed what, when, before and after.

Coverage, not examples: every documented write route must record exactly one event (right action, actor and
time), in the same transaction as the change. Refused or no-op requests record nothing.
"""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.db import models as m
from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW
from .writes import LATER, WRITES, write_routes

# what each write route must record, and who the actor is in its WRITES case
EXPECTED = {
    ("POST", "/teams/{team_id}/issues"): ("issue.created", "u3"),
    ("POST", "/issues/{issue_id}/todos"): ("todo.created", "u3"),
    ("PATCH", "/issues/{issue_id}"): ("issue.priority_changed", "u3"),
    ("POST", "/issues/{issue_id}/solve"): ("issue.solved", "u3"),
    ("POST", "/todos/{todo_id}/done"): ("todo.done", "u3"),
    ("POST", "/todos/{todo_id}/undo"): ("todo.undone", "u3"),
    ("PUT", "/measurables/{measurable_id}/weeks/{week}/score"): ("score.set", "u3"),
    ("POST", "/measurables/{measurable_id}/weeks/{week}/issue"): ("issue.raised", "u3"),
    ("POST", "/rocks/{rock_id}/issue"): ("issue.raised", "u3"),
    ("POST", "/todos/{todo_id}/issue"): ("issue.raised", "u3"),
    ("POST", "/teams/{team_id}/meetings"): ("meeting.started", "u3"),
    ("POST", "/meetings/{meeting_id}/join"): ("meeting.joined", "u2"),
    ("POST", "/meetings/{meeting_id}/advance"): ("meeting.advanced", "u3"),
    ("PUT", "/meetings/{meeting_id}/rating"): ("meeting.rated", "u3"),
    ("POST", "/meetings/{meeting_id}/end"): ("meeting.ended", "u3"),
}


@pytest.fixture(autouse=True)
def _baseline(session):
    """Committed events from concurrency tests persist (append-only); look only at this test's events."""
    global FLOOR
    FLOOR = session.scalar(select(m.AuditEvent.id).order_by(m.AuditEvent.id.desc()).limit(1)) or 0


FLOOR = 0


def events(session, **where):
    q = select(m.AuditEvent).where(m.AuditEvent.id > FLOOR)
    for k, v in where.items():
        q = q.where(getattr(m.AuditEvent, k) == v)
    session.expire_all()
    return session.scalars(q.order_by(m.AuditEvent.id)).all()


def test_every_write_route_has_an_audit_action():
    assert write_routes() <= set(EXPECTED), f"say what these record: {sorted(write_routes() - set(EXPECTED))}"


@pytest.mark.parametrize("route", sorted(WRITES))
def test_each_write_records_exactly_one_event(as_user, session, route):
    WRITES[route](as_user("u3"))
    action, actor = EXPECTED[route]
    recorded = events(session, at=LATER)
    assert [(e.action, e.actor_id, e.team_id) for e in recorded] == [(action, actor, "team_lead")], route


class TestBeforeAndAfter:
    def test_priority_change(self, as_user, session):
        as_user("u3").patch("/issues/i3", json={"priority": 1})
        (e,) = events(session, action="issue.priority_changed")
        assert (e.entity_id, e.entity_label, e.changes) == ("i3", "Support coverage on weekends", {"priority": [3, 1]})

    def test_score_change_and_first_score(self, as_user, session):
        c = as_user("u3")
        c.put("/measurables/m2/weeks/2026-11-02/score", json={"value": 2.5})
        c.put("/measurables/m1/weeks/2026-11-09/score", json={"value": 7})  # no score before
        got = {e.entity_id: (e.entity_label, e.changes) for e in events(session, action="score.set")}
        assert got == {"m2:2026-11-02": ("Customer churn, week of Nov 2", {"value": [3.0, 2.5]}),
                       "m1:2026-11-09": ("New deals closed, week of Nov 9", {"value": [None, 7.0]})}

    def test_rating_change_records_the_previous_rating(self, as_user, session):
        from .writes import live_meeting

        c = as_user("u3")
        mt = live_meeting(c, "conclude")
        c.put(f"/meetings/{mt}/rating", json={"rating": 6})
        c.put(f"/meetings/{mt}/rating", json={"rating": 8})
        assert [e.changes for e in events(session, action="meeting.rated")] == [{"rating": [None, 6]}, {"rating": [6, 8]}]

    def test_done_and_undo(self, as_user, session):
        c = as_user("u3")
        c.post("/todos/t31/done")
        c.post("/todos/t31/undo")
        done, undone = events(session, entity_id="t31")
        assert (done.action, done.changes["done_at"][0], undone.action, undone.changes["done_at"][1]) == (
            "todo.done", None, "todo.undone", None)


    def test_creations_record_what_was_set_with_names_as_they_were(self, as_user, session):
        c = as_user("u3")
        issue = c.post("/teams/team_lead/issues", json={"title": "Vendor SLA", "priority": 2}).json()["id"]
        c.post(f"/issues/{issue}/todos", json={"title": "Call vendor", "owner_id": "u4"})
        c.post(f"/issues/{issue}/solve", json={"note": "new SLA signed"})
        assert {e.action: e.changes for e in events(session)} == {
            "issue.created": {"title": [None, "Vendor SLA"], "priority": [None, 2]},
            "todo.created": {"title": [None, "Call vendor"], "owner": [None, {"id": "u4", "name": "Dana Diaz"}],
                             "due_date": [None, "2026-11-18"], "source_issue": [None, {"id": issue, "title": "Vendor SLA"}]},
            "issue.solved": {"resolution_note": [None, "new SLA signed"]},
        }

    def test_a_raised_issue_records_its_source(self, as_user, session):
        as_user("u3").post("/measurables/m3/weeks/2026-11-02/issue")
        (e,) = events(session, action="issue.raised")
        assert e.changes == {"title": [None, "Avg first response, week of Nov 2"],
                             "source_measurable_id": [None, "m3"], "source_week": [None, "2026-11-02"]}

    def test_a_meeting_is_named_by_its_start(self, as_user, session):
        as_user("u3").post("/teams/team_lead/meetings")
        (e,) = events(session, action="meeting.started")
        assert e.entity_label == "Level 10 Meeting, Nov 11, 9:00 AM"  # two meetings on one day stay apart


class TestNothingRecordedWhenNothingChanged:
    def test_refused_and_no_op_requests(self, as_user, session):
        assert as_user("u2").post("/measurables/m1/weeks/2026-11-02/issue").status_code == 200  # i5 already open
        c = as_user("u3")
        assert c.put("/measurables/m2/weeks/2026-11-02/score", json={"value": 1, "expected_version": 7}).status_code == 409
        assert c.post("/issues/i6/solve", json={"note": "again"}).status_code == 409
        assert as_user("u5").post("/teams/team_lead/issues", json={"title": "observer"}).status_code == 403
        assert as_user("u6").patch("/issues/i3", json={"priority": 1}).status_code == 404
        assert events(session) == []

    def test_the_same_value_again_writes_nothing(self, as_user, session):
        """200 with what's there, but no write: no version bump, no event (so nobody else gets a stale 409)."""
        from .writes import live_meeting

        c = as_user("u3")
        t21_done_at = session.get(m.Todo, "t21").done_at
        assert c.patch("/issues/i3", json={"priority": 3}).json()["version"] == 1  # i3 is priority 3
        assert c.put("/measurables/m2/weeks/2026-11-02/score", json={"value": 3.0}).json()["version"] == 1  # it's 3.0
        assert c.post("/todos/t21/done").json()["done_at"] is not None  # already done: its done time stays
        assert c.post("/todos/t31/undo").json()["done_at"] is None  # not done: nothing to undo
        mt = live_meeting(c, "conclude")
        c.put(f"/meetings/{mt}/rating", json={"rating": 6})
        c.put(f"/meetings/{mt}/rating", json={"rating": 6})
        session.expire_all()
        assert session.get(m.Todo, "t21").done_at == t21_done_at
        assert [e.action for e in events(session) if e.entity_type != "meeting"] == []
        assert len(events(session, action="meeting.rated")) == 1
        # a stale version is still a conflict, even for the same value (lost-update protection decides first)
        assert c.patch("/issues/i3", json={"priority": 3, "expected_version": 0}).status_code == 409
        assert c.put("/measurables/m2/weeks/2026-11-02/score", json={"value": 3.0, "expected_version": 0}).status_code == 409

    def test_a_done_time_after_now_reads_as_not_done(self, as_user, session):
        """'Before' is as of now: a done time the seed dates after now hasn't happened, so the trail doesn't reveal it."""
        session.execute(text("UPDATE todos SET done_at = '2026-11-12T15:00:00Z' WHERE id = 't31'"))  # tomorrow
        c = as_user("u3")
        assert c.post("/todos/t31/undo").json()["done_at"] is None  # not done as of now: nothing to undo
        c.post("/todos/t31/done")
        assert [e.changes["done_at"][0] for e in events(session, entity_id="t31")] == [None]

    def test_joining_twice_is_one_event(self, as_user, session):
        from .writes import live_meeting

        mt = live_meeting(as_user("u3"))
        c = as_user("u2")
        c.post(f"/meetings/{mt}/join")
        c.post(f"/meetings/{mt}/join")
        assert len(events(session, action="meeting.joined")) == 1


class TestReadingTheTrail:
    def test_newest_first_with_a_cursor(self, as_user):
        c = as_user("u2")
        for n in range(3):
            c.post("/teams/team_lead/issues", json={"title": f"audit {n}"})
        first = c.get("/teams/team_lead/audit?limit=2").json()
        assert [e["entity"]["label"] for e in first["items"]] == ["audit 2", "audit 1"]
        rest = c.get(f"/teams/team_lead/audit?limit=2&before={first['next_before']}").json()
        assert rest["items"][0]["entity"]["label"] == "audit 0"  # older events may follow (other tests commit some)
        assert first["items"][0]["actor"] == {"id": "u2", "name": "Blake Brooks"}

    def test_one_records_history_page_by_page(self, as_user):
        c = as_user("u3")
        issue = c.post("/teams/team_lead/issues", json={"title": "history"}).json()["id"]  # new: no events elsewhere
        c.patch(f"/issues/{issue}", json={"priority": 1})
        c.post(f"/issues/{issue}/solve", json={"note": "weekend rota agreed"})
        c.post("/teams/team_lead/issues", json={"title": "unrelated"})
        url = f"/teams/team_lead/audit?entity_type=issue&entity_id={issue}&limit=2"
        first = c.get(url).json()
        last = c.get(f"{url}&before={first['next_before']}").json()
        assert [e["action"] for e in first["items"] + last["items"]] == ["issue.solved", "issue.priority_changed", "issue.created"]
        assert last["next_before"] is None

    def test_teams_never_see_each_others_events(self, as_user):
        as_user("u6").post("/teams/team_sales/issues", json={"title": "sales only"})
        lead = as_user("u2").get("/teams/team_lead/audit").json()["items"]
        assert all(e["entity"]["label"] != "sales only" for e in lead)
        # even asking for that record by id from a team you can see returns nothing of it
        sales_issue = as_user("u6").get("/teams/team_sales/audit").json()["items"][0]["entity"]["id"]
        assert as_user("u2").get(f"/teams/team_lead/audit?entity_id={sales_issue}").json()["items"] == []

    def test_observers_read_outsiders_dont(self, as_user):
        as_user("u3").post("/teams/team_lead/issues", json={"title": "visible to observers"})
        assert as_user("u5").get("/teams/team_lead/audit").status_code == 200
        assert as_user("u6").get("/teams/team_lead/audit").status_code == 404

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "before=0", "entity_type=rock", "entity_id=a%00b"])
    def test_bad_queries_are_422(self, as_user, query):
        assert as_user("u2").get(f"/teams/team_lead/audit?{query}").status_code == 422


# ---------- concurrency: the losing requests record nothing ----------

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
        conn.execute(delete(m.Issue).where(m.Issue.id.like("i\\_%")))  # the audit rows stay: append-only


@pytest.mark.parametrize("url,source_key,source_id", [
    ("/measurables/m3/weeks/2026-11-02/issue", "source_measurable_id", "m3"),  # queued by the cell lock
    ("/rocks/r2/issue", "source_rock_id", "r2"),  # no lock: losers go through ON CONFLICT
])
def test_twelve_concurrent_clicks_record_one_event(real_db, url, source_key, source_id):
    """One Issue, one event: the eleven requests that lost the race (and got the winner's Issue) record nothing."""
    with real_db.connect() as conn:
        floor = conn.execute(text("SELECT coalesce(max(id), 0) FROM audit_events")).scalar()
    users = ["u1", "u2", "u3", "u4"] * 3
    barrier, ids, lock = threading.Barrier(len(users)), set(), threading.Lock()

    def click(u):
        with TestClient(app) as c:
            c.post("/auth/login", json={"user_id": u})
            barrier.wait()
            r = c.post(url)
            with lock:
                ids.add(r.json()["id"])

    ts = [threading.Thread(target=click, args=(u,)) for u in users]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    (issue_id,) = ids
    with real_db.connect() as conn:
        raised = conn.execute(text(
            "SELECT entity_id FROM audit_events WHERE id > :floor AND action = 'issue.raised' "
            "AND changes -> :key ->> 1 = :id"), {"floor": floor, "key": source_key, "id": source_id}).scalars().all()
    assert raised == [issue_id]


def test_concurrent_priority_change_audits_the_real_before(real_db):
    """Another priority change holds i3 when ours arrives: ours waits, and records that change as its 'before'."""
    from tests.conftest import wait_until_blocked_by

    result = {}
    try:
        with TestClient(app) as c:
            c.post("/auth/login", json={"user_id": "u3"})
            with real_db.connect() as other:
                tx = other.begin()
                pid = other.execute(text("SELECT pg_backend_pid()")).scalar()
                other.execute(text("UPDATE issues SET priority = 4, version = version + 1 WHERE id = 'i3'"))
                t = threading.Thread(target=lambda: result.update(r=c.patch("/issues/i3", json={"priority": 1})))
                t.start()
                wait_until_blocked_by(real_db, pid)
                tx.commit()
            t.join(10)
        assert result["r"].status_code == 200
        with real_db.connect() as conn:
            changes = conn.execute(text("SELECT changes FROM audit_events WHERE entity_id = 'i3' "
                                        "AND action = 'issue.priority_changed' ORDER BY id DESC LIMIT 1")).scalar()
        assert changes == {"priority": [4, 1]}
    finally:
        with real_db.begin() as conn:
            conn.execute(text("UPDATE issues SET priority = 3, version = 1, updated_at = NULL WHERE id = 'i3'"))
