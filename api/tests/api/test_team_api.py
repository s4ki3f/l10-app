"""Team page API (brief 2.5): Scorecard, Issues, To-Dos, Rocks. Values cross-checked with the answer key."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW

KEY = json.loads((Path(__file__).parents[1] / "rules" / "answer_key.json").read_text())["teams"]


def code(r):
    return r.json()["error"]["code"]


# ---------- Scorecard ----------

class TestScorecard:
    def test_every_cell_status_matches_the_answer_key(self, as_user):
        for user, team in (("u1", "team_lead"), ("u2", "team_sales"), ("u8", "team_ops")):
            body = as_user(user).get(f"/teams/{team}/scorecard").json()
            got = {row["id"]: {c["week_start"]: c["status"] for c in row["cells"]} for row in body["rows"]}
            assert got == KEY[team]["measurable_status"], team

    def test_shape(self, as_user):
        body = as_user("u5").get("/teams/team_lead/scorecard").json()  # observers read it too
        assert (body["weeks"][0], body["current_week"], body["last_closed_week"], len(body["weeks"])) == (
            "2026-08-17", "2026-11-09", "2026-11-02", 13)
        m3 = next(r for r in body["rows"] if r["id"] == "m3")
        assert (m3["owner"]["name"], m3["unit"], m3["goal"]) == (
            "Dana Diaz", "hours", {"type": "between", "value": None, "min": 2.0, "max": 4.0})

    def test_cells_show_their_open_issue(self, as_user):
        rows = {r["id"]: r for r in as_user("u2").get("/teams/team_lead/scorecard").json()["rows"]}
        cell = {c["week_start"]: c for c in rows["m1"]["cells"]}
        assert cell["2026-11-02"]["open_issue_id"] == "i5"
        assert {c["week_start"]: c for c in rows["m4"]["cells"]}["2026-10-26"]["open_issue_id"] is None  # i6 solved


class TestScoreEdit:
    URL = "/measurables/{m}/weeks/{w}/score"

    def test_enter_current_week_score_turns_pending_into_a_status(self, as_user):
        r = as_user("u2").put(self.URL.format(m="m1", w="2026-11-09"), json={"value": 10})
        assert (r.status_code, r.json()["status"], r.json()["value"]) == (200, "on_track", 10.0)  # boundary

    def test_last_save_wins(self, as_user):
        as_user("u2").put(self.URL.format(m="m2", w="2026-11-02"), json={"value": 2.5})
        as_user("u3").put(self.URL.format(m="m2", w="2026-11-02"), json={"value": 3.4})
        rows = {r["id"]: r for r in as_user("u4").get("/teams/team_lead/scorecard").json()["rows"]}
        cell = {c["week_start"]: c for c in rows["m2"]["cells"]}["2026-11-02"]
        assert (cell["value"], cell["status"]) == (3.4, "off_track")

    def test_fixing_a_score_makes_the_cell_ineligible_for_a_new_issue(self, as_user):
        c = as_user("u2")
        c.put(self.URL.format(m="m1", w="2026-10-26"), json={"value": 11})
        assert code(c.post("/measurables/m1/weeks/2026-10-26/issue")) == "not_off_track"

    def test_decimals_are_kept_exactly(self, as_user):
        r = as_user("u3").put(self.URL.format(m="m2", w="2026-11-09"), json={"value": "3.0001"})
        assert (r.json()["value"], r.json()["status"]) == (3.0001, "off_track")

    @pytest.mark.parametrize("week", ["2026-11-10", "2026-11-16", "2026-08-10"])
    def test_week_must_be_a_monday_on_the_scorecard(self, as_user, week):
        assert as_user("u2").put(self.URL.format(m="m1", w=week), json={"value": 1}).status_code == 422

    @pytest.mark.parametrize("payload", [{}, {"value": "abc"}, {"value": None}, {"value": "NaN"},
                                         {"value": 1e20}, {"value": "1.00001"}])
    def test_value_must_be_a_finite_number(self, as_user, payload):
        assert as_user("u2").put(self.URL.format(m="m1", w="2026-11-09"), json=payload).status_code == 422

    def test_observer_cannot_edit(self, as_user):
        assert as_user("u5").put(self.URL.format(m="m1", w="2026-11-09"), json={"value": 1}).status_code == 403


# ---------- Issues ----------

class TestIssues:
    def test_open_order_matches_the_answer_key_with_details(self, as_user):
        body = as_user("u2").get("/teams/team_lead/issues").json()
        assert [i["id"] for i in body] == KEY["team_lead"]["issue_order"]
        i5 = next(i for i in body if i["id"] == "i5")
        assert (i5["owner"]["name"], i5["priority"], i5["source"]["measurable_name"], i5["source"]["week_start"]) == (
            "Blake Brooks", 4, "New deals closed", "2026-11-02")

    def test_solved_issues_listed_separately_newest_first(self, as_user):
        body = as_user("u2").get("/teams/team_lead/issues?state=solved").json()
        assert [(i["id"], i["resolution_note"][:4]) for i in body] == [("i6", "Bank"), ("i7", "Assi")]

    def test_setting_priority_reorders(self, as_user):
        c = as_user("u2")
        assert c.patch("/issues/i4", json={"priority": 5}).json()["priority"] == 5
        assert [i["id"] for i in c.get("/teams/team_lead/issues").json()][:3] == ["i4", "i2", "i1"]  # oldest p5 first

    def test_clearing_priority_sends_it_last(self, as_user):
        c = as_user("u2")
        c.patch("/issues/i3", json={"priority": None})
        assert [i["id"] for i in c.get("/teams/team_lead/issues").json()][-2:] == ["i4", "i3"]  # both none, oldest first

    @pytest.mark.parametrize("payload", [{"priority": 6}, {"priority": 0}, {}, {"priority": "high"}])
    def test_bad_priority_is_422(self, as_user, payload):
        assert as_user("u2").patch("/issues/i3", json=payload).status_code == 422

    def test_solve_requires_a_note_and_moves_the_issue(self, as_user):
        c = as_user("u3")
        assert c.post("/issues/i1/solve", json={"note": "  "}).status_code == 422
        assert c.post("/issues/i1/solve", json={}).status_code == 422
        r = c.post("/issues/i1/solve", json={"note": "Engaged a recruiter"})
        assert (r.status_code, r.json()["solved_at"][:19], r.json()["resolution_note"]) == (
            200, "2026-11-11T15:00:00", "Engaged a recruiter")
        assert "i1" not in [i["id"] for i in c.get("/teams/team_lead/issues").json()]
        assert c.get("/teams/team_lead/issues?state=solved").json()[0]["id"] == "i1"

    def test_cannot_solve_twice_or_reprioritize_solved(self, as_user):
        c = as_user("u3")
        assert code(c.post("/issues/i6/solve", json={"note": "again"})) == "issue_solved"
        assert code(c.patch("/issues/i6", json={"priority": 3})) == "issue_solved"

    def test_solving_frees_the_cell_for_a_new_issue(self, as_user):
        c = as_user("u2")
        c.post("/issues/i5/solve", json={"note": "Pipeline review booked"})
        r = c.post("/measurables/m1/weeks/2026-11-02/issue")
        assert r.status_code == 201 and r.json()["id"] != "i5"

    def test_observer_cannot_prioritize_or_solve(self, as_user):
        c = as_user("u5")
        assert c.patch("/issues/i3", json={"priority": 1}).status_code == 403
        assert c.post("/issues/i3/solve", json={"note": "x"}).status_code == 403


# ---------- To-Dos ----------

class TestTodos:
    def test_list_flags_overdue_and_reports_last_closed_week(self, as_user):
        body = as_user("u2").get("/teams/team_lead/todos").json()
        assert sorted(t["id"] for t in body["items"] if t["overdue"]) == KEY["team_lead"]["overdue_todos"]
        assert body["completion"] == {"week_start": "2026-11-02", **KEY["team_lead"]["todo_completion"]["2026-11-02"]}
        t20 = next(t for t in body["items"] if t["id"] == "t20")
        assert t20["source_issue"] == {"id": "i7", "name": "Onboarding docs are outdated"}

    def test_week_with_nothing_due_has_no_rate(self, as_user):
        assert as_user("u6").get("/teams/team_sales/todos").json()["completion"] is None

    def test_done_and_undo(self, as_user):
        c = as_user("u4")
        done = c.post("/todos/t31/done").json()
        assert (done["done_at"][:19], done["overdue"]) == ("2026-11-11T15:00:00", False)
        undone = c.post("/todos/t31/undo").json()
        assert (undone["done_at"], undone["overdue"]) == (None, True)

    def test_late_done_does_not_change_a_closed_week(self, as_user):
        c = as_user("u3")
        c.post("/todos/t29/done")  # due Nov 8, done now (Nov 11): still not done for its week
        assert c.get("/teams/team_lead/todos").json()["completion"]["done"] == 9

    def test_observer_cannot_change_todos(self, as_user):
        assert as_user("u5").post("/todos/t31/done").status_code == 403


# ---------- Rocks ----------

class TestRocks:
    def test_status_and_progress_match_the_answer_key(self, as_user):
        for user, team in (("u1", "team_lead"), ("u6", "team_sales"), ("u9", "team_ops")):
            body = as_user(user).get(f"/teams/{team}/rocks").json()
            assert {r["id"]: {"status": r["status"], "progress_percent": r["progress_percent"]} for r in body} == KEY[team]["rocks"]

    def test_only_the_current_quarters_rocks(self, as_user):
        # every seeded Rock is Q4 2026; in Q3 the list must be empty, in Q1 2027 too
        c = as_user("u3")
        for delta in (timedelta(days=-90), timedelta(days=60)):
            app.state.clock = FrozenClock(NOW + delta)
            assert c.get("/teams/team_lead/rocks").json() == []

    def test_milestones_expand_with_past_due_flag(self, as_user):
        r2 = next(r for r in as_user("u3").get("/teams/team_lead/rocks").json() if r["id"] == "r2")
        assert len(r2["milestones"]) == 8
        assert [m["id"] for m in r2["milestones"] if m["past_due"]] == ["ms5"]
        r1 = next(r for r in as_user("u3").get("/teams/team_lead/rocks").json() if r["id"] == "r1")
        assert [m["past_due"] for m in r1["milestones"]] == [False, False, False]  # ms2 due today is not past due


def test_team_members_for_owner_pickers(as_user):
    body = as_user("u2").get("/teams/team_lead/members").json()
    assert [(u["id"], u["role"]) for u in body] == [
        ("u1", "admin"), ("u2", "team_member"), ("u3", "team_member"), ("u4", "team_member"), ("u5", "observer")]
    ops = {u["id"] for u in as_user("u8").get("/teams/team_ops/members").json()}
    assert ops == {"u1", "u4", "u8", "u9"}  # members + every Admin


class TestAsOfNowAcrossTheApi:
    """Every API path uses the same 'as of now' rule as the Issues list and the rules report.
    On Nov 4 at 12:00Z, i6 (m4 / Oct 26) exists but is not yet solved (it's solved at 15:40Z)."""

    @pytest.fixture
    def nov4(self):
        app.state.clock = FrozenClock(NOW - timedelta(days=7, hours=3))  # 2026-11-04T12:00Z

    def test_list_cell_and_make_it_an_issue_agree(self, as_user, nov4):
        c = as_user("u3")
        assert "i6" in [i["id"] for i in c.get("/teams/team_lead/issues").json()]
        rows = {r["id"]: r for r in c.get("/teams/team_lead/scorecard").json()["rows"]}
        assert {x["week_start"]: x for x in rows["m4"]["cells"]}["2026-10-26"]["open_issue_id"] == "i6"
        r = c.post("/measurables/m4/weeks/2026-10-26/issue")
        assert (r.status_code, r.json()["id"]) == (200, "i6")  # no duplicate for the cell

    def test_an_issue_solved_later_can_be_solved_or_reprioritized_now(self, as_user, nov4):
        c = as_user("u3")
        assert c.patch("/issues/i6", json={"priority": 4}).status_code == 200
        assert c.post("/issues/i6/solve", json={"note": "fixed earlier"}).status_code == 200

    def test_summary_of_a_meeting_that_has_not_ended_yet_is_409(self, as_user):
        app.state.clock = FrozenClock(NOW - timedelta(days=10))  # Nov 1: mt2 is on Nov 4
        assert as_user("u2").get("/meetings/mt2/summary").json()["error"]["code"] == "meeting_live"
