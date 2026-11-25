"""Rule tests (brief 2.6). One named test per trap in the seed, each with its boundary case.

Expected values come from answer_key.json, worked by hand from seed.json before the
code existed. If code and key disagree, re-check the seed by hand before changing either.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.rules import engine
from app.rules.dataset import dataset_from_seed
from app.rules.engine import Goal, Issue, Meeting, Milestone, Rock, Todo
from app.rules.report import build_report
from app.rules.weeks import parse_utc, scorecard_weeks, week_bounds, week_of

ROOT = Path(__file__).resolve().parents[3]
SEED = json.loads((ROOT / "seed.json").read_text())
KEY = json.loads((Path(__file__).parent / "answer_key.json").read_text())
NOW = parse_utc("2026-11-11T15:00:00Z")
DATA = dataset_from_seed(SEED)


def utc(s: str) -> datetime:
    return parse_utc(s)


def todo(id="t", due="2026-11-06", done=None, created="2026-10-01T00:00:00Z"):
    return Todo(id, "team", date.fromisoformat(due), utc(created), utc(done) if done else None)


# ---------- time and weeks (Problem 1) ----------

class TestWeeks:
    def test_week_of_dst_week_is_169_hours(self):
        start, end = week_bounds(date(2026, 10, 26))
        assert (end - start).total_seconds() == 169 * 3600

    def test_t11_sunday_2330_chicago_after_dst_is_in_week_of_oct_26(self):
        # 05:30Z on Nov 2 is 23:30 Sunday Nov 1 CST (UTC-6, DST ended that morning)
        assert week_of(utc("2026-11-02T05:30:00Z")) == date(2026, 10, 26)

    def test_t28_one_minute_before_monday_is_previous_week(self):
        assert week_of(utc("2026-11-09T05:59:00Z")) == date(2026, 11, 2)

    def test_t29_one_minute_after_monday_is_next_week(self):
        assert week_of(utc("2026-11-09T06:01:00Z")) == date(2026, 11, 9)

    def test_summer_offset_is_five_hours(self):
        # 04:59Z Monday Aug 17 is still Sunday 23:59 CDT
        assert week_of(utc("2026-08-17T04:59:00Z")) == date(2026, 8, 10)
        assert week_of(utc("2026-08-17T05:00:00Z")) == date(2026, 8, 17)

    def test_scorecard_window_is_13_weeks_ending_this_week(self):
        weeks = scorecard_weeks(NOW)
        assert (weeks[0], weeks[-1], len(weeks)) == (date(2026, 8, 17), date(2026, 11, 9), 13)

    def test_naive_datetimes_are_rejected(self):
        with pytest.raises(ValueError):
            week_of(datetime(2026, 11, 2, 5, 30))


# ---------- Measurable status ----------

class TestMeasurableStatus:
    CUR = date(2026, 11, 9)
    CLOSED = date(2026, 11, 2)

    @pytest.mark.parametrize("goal,value,expected", [
        (Goal("at_least", Decimal(10)), Decimal(10), "on_track"),     # boundary
        (Goal("at_least", Decimal(10)), Decimal("9.99"), "off_track"),
        (Goal("at_most", Decimal("3.0")), Decimal("3.0"), "on_track"),  # boundary (m2 Nov 2)
        (Goal("at_most", Decimal("3.0")), Decimal("3.1"), "off_track"),
        (Goal("between", min=Decimal(2), max=Decimal(4)), Decimal(2), "on_track"),   # lower boundary
        (Goal("between", min=Decimal(2), max=Decimal(4)), Decimal(4), "on_track"),   # upper boundary
        (Goal("between", min=Decimal(2), max=Decimal(4)), Decimal("4.1"), "off_track"),
        (Goal("exactly", Decimal(1)), Decimal(1), "on_track"),
        (Goal("exactly", Decimal(1)), Decimal(2), "off_track"),
    ])
    def test_goal_types_and_boundaries(self, goal, value, expected):
        assert engine.measurable_status(goal, value, self.CLOSED, self.CUR) == expected

    def test_closed_week_without_score_is_off_track(self):  # m5 Oct 19 / Nov 2
        assert engine.measurable_status(Goal("at_least", Decimal(500)), None, self.CLOSED, self.CUR) == "off_track"

    def test_current_week_without_score_is_pending(self):
        assert engine.measurable_status(Goal("at_least", Decimal(10)), None, self.CUR, self.CUR) == "pending"

    def test_current_week_with_score_is_judged(self):  # m5 Nov 9 = 520
        assert engine.measurable_status(Goal("at_least", Decimal(500)), Decimal(520), self.CUR, self.CUR) == "on_track"


# ---------- To-Do completion ----------

class TestTodoCompletion:
    WEEK = date(2026, 11, 2)

    def test_exactly_90_percent_meets_target(self):
        todos = [todo(f"t{i}", done="2026-11-05T12:00:00Z") for i in range(9)] + [todo("late")]
        assert engine.todo_completion(todos, self.WEEK, NOW) == {"due": 10, "done": 9, "rate_percent": 90, "meets_target": True}

    def test_just_under_90_percent_misses_even_if_it_rounds_to_90(self):
        # 89.6% rounds to 90 but the exact ratio is below target
        todos = [todo(f"t{i}", done="2026-11-05T12:00:00Z") for i in range(43)] + [todo(f"x{i}") for i in range(5)]
        result = engine.todo_completion(todos, self.WEEK, NOW)
        assert (result["rate_percent"], result["meets_target"]) == (90, False)

    def test_done_one_minute_before_week_end_counts(self):  # t28
        assert engine.todo_completion([todo(done="2026-11-09T05:59:00Z")], self.WEEK, NOW)["done"] == 1

    def test_done_one_minute_after_week_end_does_not_count(self):  # t29
        assert engine.todo_completion([todo(done="2026-11-09T06:01:00Z")], self.WEEK, NOW)["done"] == 0

    def test_done_early_counts(self):
        assert engine.todo_completion([todo(done="2026-10-20T12:00:00Z")], self.WEEK, NOW)["done"] == 1

    def test_zero_due_is_none(self):
        assert engine.todo_completion([todo(due="2026-10-01")], self.WEEK, NOW) is None

    def test_rate_rounds_half_up(self):  # 2/3 → 67
        todos = [todo("a", done="2026-11-03T12:00:00Z"), todo("b", done="2026-11-03T12:00:00Z"), todo("c")]
        assert engine.todo_completion(todos, self.WEEK, NOW)["rate_percent"] == 67


class TestOverdue:
    def test_due_today_is_not_overdue(self):  # t30
        assert not engine.is_overdue(todo(due="2026-11-11"), NOW)

    def test_due_yesterday_is_overdue(self):  # t31
        assert engine.is_overdue(todo(due="2026-11-10"), NOW)

    def test_today_is_chicago_date_not_utc_date(self):
        # 03:00Z Nov 12 is still Nov 11 in Chicago, so a To-Do due Nov 11 is not overdue yet
        assert not engine.is_overdue(todo(due="2026-11-11"), utc("2026-11-12T03:00:00Z"))

    def test_done_is_never_overdue(self):
        assert not engine.is_overdue(todo(due="2026-11-01", done="2026-11-02T00:00:00Z"), NOW)


# ---------- Rocks ----------

def rock(status="on_track"):
    return Rock("r", "team", "2026-Q4", status)


def ms(due, done=False):
    return Milestone("m", "r", date.fromisoformat(due), done)


class TestRocks:
    def test_milestone_due_today_is_not_past_due(self):  # r1
        assert engine.rock_status(rock(), [ms("2026-11-11")], NOW) == "on_track"

    def test_unfinished_past_due_milestone_overrides_seeded_on_track(self):  # r2
        assert engine.rock_status(rock(), [ms("2026-11-10")], NOW) == "off_track"

    def test_done_past_milestone_does_not_make_off_track(self):
        assert engine.rock_status(rock(), [ms("2026-11-10", done=True)], NOW) == "on_track"

    def test_seeded_complete_stays_complete(self):
        assert engine.rock_status(rock("complete"), [ms("2026-10-01")], NOW) == "complete"

    def test_seeded_off_track_without_milestones(self):  # r4
        assert engine.rock_status(rock("off_track"), [], NOW) == "off_track"

    def test_progress_one_of_eight_rounds_half_up_to_13(self):  # r2
        assert engine.rock_progress(rock(), [ms("2026-12-01", True)] + [ms("2026-12-01")] * 7) == 13

    def test_progress_without_milestones(self):  # r3, r5
        assert engine.rock_progress(rock("complete"), []) == 100
        assert engine.rock_progress(rock(), []) == 0


# ---------- Issue order ----------

def issue(id, priority, created, solved=None):
    return Issue(id, "team", utc(created), priority, utc(solved) if solved else None)


class TestIssueOrder:
    def test_priority_desc_then_oldest_then_no_priority_last(self):
        issues = [
            issue("none", None, "2026-01-01T00:00:00Z"),
            issue("p5new", 5, "2026-10-20T00:00:00Z"),
            issue("p1", 1, "2026-10-01T00:00:00Z"),
            issue("p5old", 5, "2026-10-14T00:00:00Z"),
        ]
        assert [i.id for i in engine.issue_order(issues, NOW)] == ["p5old", "p5new", "p1", "none"]

    def test_solved_issues_are_excluded(self):
        assert engine.issue_order([issue("s", 5, "2026-10-01T00:00:00Z", solved="2026-10-02T00:00:00Z")], NOW) == []

    def test_same_priority_same_time_breaks_tie_by_id(self):
        issues = [issue("b", 3, "2026-10-01T00:00:00Z"), issue("a", 3, "2026-10-01T00:00:00Z")]
        assert [i.id for i in engine.issue_order(issues, NOW)] == ["a", "b"]


# ---------- Meeting rating ----------

class TestMeetingRating:
    def test_8_25_rounds_half_up_to_8_3(self):  # mt2 — float round() gives 8.2
        assert engine.meeting_rating([8, 9, 8, 8]) == Decimal("8.3")

    def test_whole_average_keeps_one_decimal(self):  # mt1
        assert str(engine.meeting_rating([8, 9, 7])) == "8.0"

    def test_no_ratings_is_none(self):
        assert engine.meeting_rating([]) is None


class TestMeetingSummary:
    def test_reviewed_week_is_the_week_before_the_meeting(self):
        m = Meeting("mt", "team", utc("2026-10-28T15:00:00Z"), utc("2026-10-28T16:15:00Z"))
        assert engine.scorecard_week_for(m) == date(2026, 10, 19)


# ---------- the whole report against the answer key ----------

def test_rules_report_matches_hand_worked_answer_key():
    report = build_report(DATA, NOW)
    expected = {k: v for k, v in KEY.items() if not k.startswith("_")}
    assert report == expected


def test_report_at_a_summer_date_uses_summer_weeks():
    """Nothing is hard-coded to the default APP_NOW."""
    now = utc("2026-08-20T15:00:00Z")  # Thursday, CDT
    report = build_report(DATA, now)
    lead = report["teams"]["team_lead"]
    assert list(lead["measurable_status"]["m1"])[-1] == "2026-08-17"
    assert lead["measurable_status"]["m1"]["2026-08-17"] == "on_track"      # score 12 exists
    assert lead["measurable_status"]["m1"]["2026-08-10"] == "off_track"     # closed, no score
    assert lead["issue_order"] == []      # i4, the oldest Issue, was created Sep 1
    assert lead["meetings"] == {}         # neither meeting had happened
    assert lead["rocks"] == {}            # Q3: no Q4 rocks yet


def test_report_as_of_now_ignores_future_completion():
    """At Nov 1 noon, t11 (done Nov 2 05:30Z) is not done yet and i6 is not solved."""
    now = utc("2026-11-01T17:00:00Z")
    report = build_report(DATA, now)
    lead = report["teams"]["team_lead"]
    assert "t11" in lead["overdue_todos"]
    assert lead["todo_completion"]["2026-10-26"]["done"] == 1  # only t10
    assert list(report["teams"]["team_lead"]["meetings"]) == ["mt1"]
