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
