"""Bonus: Make it an Issue from an off-track Rock or an overdue To-Do, with the same no-duplicates rule.

Seed at APP_NOW: r2 off track (ms5 past due), r4 seeded off track, r1 on track (ms2 due today);
t12 and t31 overdue, t30 due today (not overdue), t32 not due yet.
"""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.db import models as m
from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW


def code(r):
    return r.json()["error"]["code"]


class TestFromRock:
    def test_off_track_rock_becomes_an_issue_owned_by_the_clicker(self, as_user):
        r = as_user("u3").post("/rocks/r2/issue")
        body = r.json()
        assert (r.status_code, body["title"], body["owner"]["id"]) == (201, "Rock off track: Hire VP of Sales", "u3")
        assert body["source_rock"] == {"id": "r2", "title": "Hire VP of Sales"} and body["source"] is None

    def test_second_click_returns_the_same_issue(self, as_user):
        first = as_user("u3").post("/rocks/r4/issue").json()
        r = as_user("u2").post("/rocks/r4/issue")
        assert (r.status_code, r.json()["id"], r.json()["owner"]["id"]) == (200, first["id"], "u3")

    def test_the_rock_shows_its_open_issue(self, as_user):
        issue = as_user("u3").post("/rocks/r2/issue").json()
        rocks = {x["id"]: x for x in as_user("u3").get("/teams/team_lead/rocks").json()}
        assert (rocks["r2"]["open_issue_id"], rocks["r1"]["open_issue_id"]) == (issue["id"], None)

    def test_on_track_rock_is_409(self, as_user):
        assert code(as_user("u3").post("/rocks/r1/issue")) == "not_off_track"

    def test_solving_frees_the_rock_for_a_new_issue(self, as_user):
        c = as_user("u3")
        first = c.post("/rocks/r2/issue").json()
        c.post(f"/issues/{first['id']}/solve", json={"note": "recruiter engaged"})
        r = c.post("/rocks/r2/issue")
        assert r.status_code == 201 and r.json()["id"] != first["id"]

    def test_observer_403_outsider_404(self, as_user):
        assert as_user("u5").post("/rocks/r2/issue").status_code == 403
        assert as_user("u6").post("/rocks/r2/issue").status_code == 404


class TestFromTodo:
    def test_overdue_todo_becomes_an_issue_and_shows_it(self, as_user):
        c = as_user("u2")
        r = c.post("/todos/t31/issue")
        assert (r.status_code, r.json()["title"]) == (201, "Overdue To-Do: Send portal beta invites")
        assert r.json()["source_todo"] == {"id": "t31", "title": "Send portal beta invites"}
        todos = {t["id"]: t for t in c.get("/teams/team_lead/todos").json()["items"]}
        assert todos["t31"]["open_issue_id"] == r.json()["id"]

    def test_second_click_returns_the_same_issue(self, as_user):
        first = as_user("u2").post("/todos/t12/issue").json()
        assert as_user("u3").post("/todos/t12/issue").json()["id"] == first["id"]

    @pytest.mark.parametrize("todo", ["t30", "t32", "t21"])  # due today, not due yet, done
    def test_not_overdue_is_409(self, as_user, todo):
        assert code(as_user("u2").post(f"/todos/{todo}/issue")) == "not_overdue"

    def test_issues_list_names_the_source(self, as_user):
        c = as_user("u2")
        c.post("/todos/t31/issue")
        c.post("/rocks/r2/issue")
        sources = {(i["source_todo"] or {}).get("id") or (i["source_rock"] or {}).get("id")
                   for i in c.get("/teams/team_lead/issues").json()}
        assert {"t31", "r2"} <= sources


class TestDatabaseRules:
    def test_an_issue_has_at_most_one_source(self, db):
        from tests.db.test_constraints import rejects

        rejects(db, "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_measurable_id, source_week, "
                    "source_rock_id) VALUES ('ix','team_lead','x','u2',now(),'m1','2026-10-26','r2')")

    def test_one_open_issue_per_rock_and_per_todo(self, db):
        from tests.db.test_constraints import rejects

        for col, ref in (("source_rock_id", "r2"), ("source_todo_id", "t31")):
            db.execute(text(f"INSERT INTO issues (id, team_id, title, owner_id, created_at, {col}) "
                            f"VALUES ('i_a','team_lead','a','u2',now(),'{ref}')"))
            rejects(db, f"INSERT INTO issues (id, team_id, title, owner_id, created_at, {col}) "
                        f"VALUES ('i_b','team_lead','b','u2',now(),'{ref}')")
            db.execute(text("DELETE FROM issues WHERE id = 'i_a'"))

    def test_source_rock_and_todo_stay_in_the_team(self, db):
        from tests.db.test_constraints import rejects

        rejects(db, "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_rock_id) "
                    "VALUES ('ix','team_ops','x','u8',now(),'r2')")  # r2 is Leadership's
        rejects(db, "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_todo_id) "
                    "VALUES ('ix','team_ops','x','u8',now(),'t31')")


# ---------- concurrency: the same rule as Scorecard cells ----------

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
        conn.execute(delete(m.Issue).where(m.Issue.id.like("i\\_%")))


@pytest.mark.parametrize("url", ["/rocks/r2/issue", "/todos/t31/issue"])
def test_concurrent_clicks_make_one_issue(real_db, url):
    users = ["u1", "u2", "u3", "u4"] * 2
    barrier, out, lock = threading.Barrier(len(users)), [], threading.Lock()

    def click(u):
        with TestClient(app) as c:
            c.post("/auth/login", json={"user_id": u})
            barrier.wait()
            r = c.post(url)
            with lock:
                out.append((r.status_code, r.json().get("id")))

    ts = [threading.Thread(target=click, args=(u,)) for u in users]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(s for s, _ in out) == [200] * (len(users) - 1) + [201] and len({i for _, i in out}) == 1, out


# ---------- eligibility can't go stale between the check and the insert (B2 review) ----------

def _race(real_db, competing_sql, request):
    from tests.conftest import wait_until_blocked_by

    result = {}
    with TestClient(app) as c:
        c.post("/auth/login", json={"user_id": "u3"})
        with real_db.connect() as other:
            tx = other.begin()
            pid = other.execute(text("SELECT pg_backend_pid()")).scalar()
            other.execute(text(competing_sql))  # holds the row, not committed
            t = threading.Thread(target=lambda: result.update(r=request(c)))
            t.start()
            wait_until_blocked_by(real_db, pid)  # the request is queued behind the competing change
            tx.commit()
        t.join(10)
    return result["r"]


def test_todo_marked_done_meanwhile_is_not_made_an_overdue_issue(real_db):
    try:
        r = _race(real_db, "UPDATE todos SET done_at = '2026-11-11T14:59:00Z' WHERE id = 't31'",
                  lambda c: c.post("/todos/t31/issue"))
        assert (r.status_code, r.json()["error"]["code"]) == (409, "not_overdue")
    finally:
        with real_db.begin() as conn:
            conn.execute(text("UPDATE todos SET done_at = NULL WHERE id = 't31'"))


def test_score_fixed_meanwhile_is_not_made_an_issue(real_db):
    try:
        r = _race(real_db, "UPDATE scores SET value = 11 WHERE measurable_id = 'm1' AND week_start = '2026-10-26'",
                  lambda c: c.post("/measurables/m1/weeks/2026-10-26/issue"))
        assert (r.status_code, r.json()["error"]["code"]) == (409, "not_off_track")
    finally:
        with real_db.begin() as conn:
            conn.execute(text("UPDATE scores SET value = 9 WHERE measurable_id = 'm1' AND week_start = '2026-10-26'"))


def test_a_rock_from_another_quarter_is_409(as_user):
    from datetime import timedelta

    app.state.clock = FrozenClock(NOW + timedelta(days=60))  # 2027-Q1: the Rocks list is empty, so is this
    r = as_user("u3").post("/rocks/r4/issue")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "not_current_quarter")


# ---------- empty Scorecard cells: a row lock needs a row, so cell writes take the cell's own lock ----------

def _cell_writer_sql(measurable_id: str, week: str, value: float) -> str:
    """What a concurrent put_score does on an empty cell: take the cell lock, insert the first score."""
    from datetime import date

    from app.api.raise_issue import cell_lock_key

    key = cell_lock_key(measurable_id, date.fromisoformat(week))
    return (f"SELECT pg_advisory_xact_lock(hashtextextended('{key}', 0)); "
            f"INSERT INTO scores (measurable_id, week_start, value) VALUES ('{measurable_id}', '{week}', {value})")


def test_first_score_saved_meanwhile_on_an_empty_cell_is_not_made_an_issue(real_db):
    try:  # m5 has no score for Nov 2: a closed week with no score is off track — until the first score lands
        r = _race(real_db, _cell_writer_sql("m5", "2026-11-02", 600), lambda c: c.post("/measurables/m5/weeks/2026-11-02/issue"))
        assert (r.status_code, r.json()["error"]["code"]) == (409, "not_off_track")
    finally:
        with real_db.begin() as conn:
            conn.execute(text("DELETE FROM scores WHERE measurable_id = 'm5' AND week_start = '2026-11-02'"))


def test_racing_first_saves_audit_the_real_before(real_db):
    try:  # m1 has no score for Nov 9 yet; another first save lands while ours waits
        r = _race(real_db, _cell_writer_sql("m1", "2026-11-09", 7), lambda c: c.put("/measurables/m1/weeks/2026-11-09/score", json={"value": 9}))
        assert r.status_code == 200
        with real_db.connect() as conn:
            changes = conn.execute(text("SELECT changes FROM audit_events WHERE entity_id = 'm1:2026-11-09' "
                                        "ORDER BY id DESC LIMIT 1")).scalar()
        assert changes == {"value": [7.0, 9.0]}  # not [null, 9]: the first save is what we overwrote
    finally:
        with real_db.begin() as conn:
            conn.execute(text("DELETE FROM scores WHERE measurable_id = 'm1' AND week_start = '2026-11-09'"))
