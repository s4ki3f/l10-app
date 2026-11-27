"""Bonus: lost-update protection for scores and Issue priority.

A save that names the version it saw only goes through if nobody saved in between; otherwise 409
stale_write with what's there now. Without expected_version, the brief's 'last save wins' still applies.
"""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW

SCORE = "/measurables/{m}/weeks/{w}/score"


def cell(c, m, w):
    rows = {r["id"]: r for r in c.get("/teams/team_lead/scorecard").json()["rows"]}
    return {x["week_start"]: x for x in rows[m]["cells"]}[w]


class TestScores:
    def test_cells_and_issues_report_their_version(self, as_user):
        c = as_user("u2")
        assert cell(c, "m2", "2026-11-02")["version"] == 1
        assert cell(c, "m1", "2026-11-09")["version"] == 0  # no score yet
        assert {i["id"]: i["version"] for i in c.get("/teams/team_lead/issues").json()}["i3"] == 1

    def test_second_save_on_the_same_version_is_409_and_keeps_the_first(self, as_user):
        blake = as_user("u2")
        r = blake.put(SCORE.format(m="m2", w="2026-11-02"), json={"value": 2.5, "expected_version": 1})
        assert (r.status_code, r.json()["version"]) == (200, 2)
        casey = as_user("u3")  # loaded version 1 before Blake saved
        r = casey.put(SCORE.format(m="m2", w="2026-11-02"), json={"value": 3.4, "expected_version": 1})
        assert (r.status_code, r.json()["error"]["code"]) == (409, "stale_write")
        assert r.json()["error"]["current"] == {"value": 2.5, "version": 2, "updated_by": "Blake Brooks"}
        assert cell(casey, "m2", "2026-11-02")["value"] == 2.5  # Blake's change survived

    def test_two_people_creating_the_same_cell(self, as_user):
        assert as_user("u2").put(SCORE.format(m="m1", w="2026-11-09"), json={"value": 11, "expected_version": 0}).status_code == 200
        r = as_user("u3").put(SCORE.format(m="m1", w="2026-11-09"), json={"value": 7, "expected_version": 0})
        assert (r.status_code, r.json()["error"]["current"]["value"]) == (409, 11.0)

    def test_naming_a_version_for_a_cell_that_has_no_score_is_409(self, as_user):
        r = as_user("u2").put(SCORE.format(m="m1", w="2026-11-09"), json={"value": 3, "expected_version": 4})
        assert r.json()["error"]["current"] == {"value": None, "version": 0, "updated_by": None}

    def test_without_a_version_last_save_wins_and_still_bumps(self, as_user):
        c = as_user("u2")
        c.put(SCORE.format(m="m2", w="2026-11-02"), json={"value": 2.5})
        r = c.put(SCORE.format(m="m2", w="2026-11-02"), json={"value": 2.7})
        assert (r.status_code, r.json()["version"], cell(c, "m2", "2026-11-02")["value"]) == (200, 3, 2.7)

    @pytest.mark.parametrize("v", [-1, "1", True, 1.5])
    def test_expected_version_must_be_a_non_negative_integer(self, as_user, v):
        assert as_user("u2").put(SCORE.format(m="m2", w="2026-11-02"), json={"value": 1, "expected_version": v}).status_code == 422


class TestPriority:
    def test_stale_priority_change_is_409(self, as_user):
        assert as_user("u2").patch("/issues/i3", json={"priority": 1, "expected_version": 1}).json()["version"] == 2
        r = as_user("u3").patch("/issues/i3", json={"priority": 5, "expected_version": 1})
        assert (r.status_code, r.json()["error"]["current"]) == (409, {"priority": 1, "version": 2})

    def test_a_solve_counts_as_a_change(self, as_user):
        c = as_user("u2")
        solved = c.post("/issues/i3/solve", json={"note": "covered"}).json()
        assert solved["version"] == 2

    def test_without_a_version_last_save_wins(self, as_user):
        c = as_user("u2")
        c.patch("/issues/i3", json={"priority": 1})
        assert c.patch("/issues/i3", json={"priority": 2}).json()["priority"] == 2


# ---------- real concurrency: N saves with the same version, exactly one wins ----------

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
    with committed_engine.begin() as conn:  # restore the seeded cell
        conn.execute(text("UPDATE scores SET value = 3.0, version = 1, updated_at = NULL, updated_by = NULL "
                          "WHERE measurable_id = 'm2' AND week_start = '2026-11-02'"))


def test_concurrent_saves_on_one_version_exactly_one_wins(real_db):
    users = ["u1", "u2", "u3", "u4"] * 2
    barrier, out, lock = threading.Barrier(len(users)), [], threading.Lock()

    def save(i, u):
        with TestClient(app) as c:
            c.post("/auth/login", json={"user_id": u})
            barrier.wait()
            r = c.put(SCORE.format(m="m2", w="2026-11-02"), json={"value": 2 + i / 10, "expected_version": 1})
            with lock:
                out.append(r.status_code)

    ts = [threading.Thread(target=save, args=(i, u)) for i, u in enumerate(users)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(out) == [200] + [409] * (len(users) - 1), out
    with real_db.connect() as conn:
        assert conn.execute(text("SELECT version FROM scores WHERE measurable_id='m2' AND week_start='2026-11-02'")).scalar() == 2
