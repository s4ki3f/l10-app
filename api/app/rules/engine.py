"""Business rules (brief section 2.6) as pure functions.

Data and "now" go in, a value comes out: no database, no clock. The API, the
meeting summary and the rules report all call these, so they can't disagree.

"As of now": anything done, solved or created after `now` has not happened yet.
That keeps the report correct when it is run at an earlier date than the seed.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .weeks import local_date, week_bounds, week_of

Status = Literal["on_track", "off_track", "pending"]
RockStatus = Literal["on_track", "off_track", "complete"]
GoalType = Literal["at_least", "at_most", "between", "exactly"]

TODO_TARGET_NUM, TODO_TARGET_DEN = 9, 10  # 90%


# ---------- plain data the rules work on ----------

@dataclass(frozen=True)
class Goal:
    type: GoalType
    value: Decimal | None = None
    min: Decimal | None = None
    max: Decimal | None = None


@dataclass(frozen=True)
class Measurable:
    id: str
    team_id: str
    name: str
    goal: Goal


@dataclass(frozen=True)
class Todo:
    id: str
    team_id: str
    due_date: date
    created_at: datetime
    done_at: datetime | None = None


@dataclass(frozen=True)
class Milestone:
    id: str
    rock_id: str
    due_date: date
    done: bool


@dataclass(frozen=True)
class Rock:
    id: str
    team_id: str
    quarter: str
    seeded_status: RockStatus


@dataclass(frozen=True)
class Issue:
    id: str
    team_id: str
    created_at: datetime
    priority: int | None = None
    solved_at: datetime | None = None
    source_measurable_id: str | None = None
    source_week: date | None = None


@dataclass(frozen=True)
class Meeting:
    id: str
    team_id: str
    started_at: datetime
    ended_at: datetime | None
    attendee_ids: tuple[str, ...] = ()
    ratings: dict[str, int] = field(default_factory=dict)


# ---------- rounding ----------

def round_half_up(value: Decimal, places: int = 0) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def whole_percent(numerator: int, denominator: int) -> int:
    """numerator/denominator as a whole percent, half up (1/8 → 13)."""
    return int(round_half_up(Decimal(numerator) * 100 / Decimal(denominator)))


# ---------- "as of now" helpers ----------

def happened(moment: datetime | None, now: datetime) -> bool:
    return moment is not None and moment <= now


# ---------- Measurable status ----------

def goal_met(goal: Goal, value: Decimal) -> bool:
    """Boundaries are on track."""
    match goal.type:
        case "at_least":
            return value >= goal.value
        case "at_most":
            return value <= goal.value
        case "exactly":
            return value == goal.value
        case "between":
            return goal.min <= value <= goal.max
    raise ValueError(f"unknown goal type {goal.type}")


def measurable_status(goal: Goal, value: Decimal | None, week_start: date, current_week: date) -> Status:
    """A closed week with no score is off track; the current (or a future) week with no score is pending."""
    if value is None:
        return "off_track" if week_start < current_week else "pending"
    return "on_track" if goal_met(goal, value) else "off_track"


# ---------- To-Dos ----------

def todo_week(todo: Todo) -> date:
    """The week a To-Do is due in (due dates are company-time calendar dates)."""
    return todo.due_date - timedelta(days=todo.due_date.weekday())


def todo_completion(todos: list[Todo], week_start: date, now: datetime) -> dict | None:
    """To-Dos done by the end of the week ÷ To-Dos due that week. None when nothing was due.

    Done after its due week counts as not done. meets_target compares the exact
    ratio (done*10 >= due*9), never the rounded percent.
    """
    _, week_end = week_bounds(week_start)
    due = [t for t in todos if todo_week(t) == week_start and t.created_at <= now]
    if not due:
        return None
    done = sum(1 for t in due if happened(t.done_at, now) and t.done_at < week_end)
    return {
        "due": len(due),
        "done": done,
        "rate_percent": whole_percent(done, len(due)),
        "meets_target": done * TODO_TARGET_DEN >= len(due) * TODO_TARGET_NUM,
    }


def is_overdue(todo: Todo, now: datetime) -> bool:
    """Not done and due before today (company time). Due today is not overdue."""
    return not happened(todo.done_at, now) and todo.due_date < local_date(now)


# ---------- Rocks ----------

def rock_status(rock: Rock, milestones: list[Milestone], now: datetime) -> RockStatus:
    """A Milestone due today is not past due."""
    if rock.seeded_status == "complete":
        return "complete"
    today = local_date(now)
    if rock.seeded_status == "off_track" or any(not m.done and m.due_date < today for m in milestones):
        return "off_track"
    return "on_track"


def rock_progress(rock: Rock, milestones: list[Milestone]) -> int:
    """Done ÷ all Milestones as a whole percent. No Milestones: 0, or 100 once complete."""
    if not milestones:
        return 100 if rock.seeded_status == "complete" else 0
    return whole_percent(sum(m.done for m in milestones), len(milestones))


# ---------- Issues ----------

def is_unsolved(issue: Issue, now: datetime) -> bool:
    return issue.created_at <= now and not happened(issue.solved_at, now)


def issue_sort_key(issue: Issue) -> tuple:
    """Priority 5 first down to 1, no priority last; oldest first; id breaks ties."""
    return (-(issue.priority or 0), issue.created_at, issue.id)


def issue_order(issues: list[Issue], now: datetime) -> list[Issue]:
    return sorted((i for i in issues if is_unsolved(i, now)), key=issue_sort_key)


# ---------- Meetings ----------

def meeting_rating(ratings: list[int]) -> Decimal | None:
    """Average of submitted ratings to one decimal, half up. None when no one rated."""
    if not ratings:
        return None
    return round_half_up(Decimal(sum(ratings)) / len(ratings), 1)


def scorecard_week_for(meeting: Meeting) -> date:
    """The last closed week when the meeting started."""
    return week_of(meeting.started_at) - timedelta(weeks=1)


def during(moment: datetime | None, meeting: Meeting) -> bool:
    return moment is not None and meeting.ended_at is not None and meeting.started_at <= moment <= meeting.ended_at


def meeting_summary(
    meeting: Meeting,
    measurables: list[Measurable],
    scores: dict[tuple[str, date], Decimal],
    issues: list[Issue],
    todos: list[Todo],
) -> dict:
    """What an ended meeting reviewed and produced. Same function for live and seeded meetings."""
    week = scorecard_week_for(meeting)
    meeting_week = week_of(meeting.started_at)
    return {
        "scorecard_week": week,
        "measurable_status": {
            m.id: measurable_status(m.goal, scores.get((m.id, week)), week, meeting_week)
            for m in measurables if m.team_id == meeting.team_id
        },
        "issues_solved": sorted(i.id for i in issues if i.team_id == meeting.team_id and during(i.solved_at, meeting)),
        "todos_created": sorted(t.id for t in todos if t.team_id == meeting.team_id and during(t.created_at, meeting)),
        "attendee_ids": sorted(meeting.attendee_ids),
        "rating": meeting_rating(list(meeting.ratings.values())),
    }
