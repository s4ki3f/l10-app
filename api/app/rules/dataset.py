"""Everything the rules need, as plain data. Built from seed.json or from the database."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .engine import Goal, Issue, Measurable, Meeting, Milestone, Rock, Todo
from .weeks import parse_utc


@dataclass(frozen=True)
class Dataset:
    team_ids: list[str]
    measurables: list[Measurable]
    scores: dict[tuple[str, date], Decimal]
    rocks: list[Rock]
    milestones: list[Milestone]
    issues: list[Issue]
    todos: list[Todo]
    meetings: list[Meeting]


def _dec(value) -> Decimal | None:
    # str() first so 3.1 stays 3.1 rather than its binary float expansion
    return None if value is None else Decimal(str(value))


def _ts(value):
    return None if value is None else parse_utc(value)


def goal_from_seed(goal: dict) -> Goal:
    return Goal(type=goal["type"], value=_dec(goal.get("value")), min=_dec(goal.get("min")), max=_dec(goal.get("max")))


def dataset_from_seed(seed: dict) -> Dataset:
    return Dataset(
        team_ids=[t["id"] for t in seed["teams"]],
        measurables=[Measurable(m["id"], m["team_id"], m["name"], goal_from_seed(m["goal"])) for m in seed["measurables"]],
        scores={(s["measurable_id"], date.fromisoformat(s["week_start"])): _dec(s["value"]) for s in seed["scores"]},
        rocks=[Rock(r["id"], r["team_id"], r["quarter"], r["status"]) for r in seed["rocks"]],
        milestones=[Milestone(m["id"], m["rock_id"], date.fromisoformat(m["due_date"]), m["done"]) for m in seed["milestones"]],
        issues=[
            Issue(
                id=i["id"], team_id=i["team_id"], created_at=_ts(i["created_at"]), priority=i["priority"],
                solved_at=_ts(i["solved_at"]),
                source_measurable_id=(i["source"] or {}).get("measurable_id"),
                source_week=date.fromisoformat(i["source"]["week_start"]) if i["source"] else None,
            )
            for i in seed["issues"]
        ],
        todos=[
            Todo(t["id"], t["team_id"], date.fromisoformat(t["due_date"]), _ts(t["created_at"]), _ts(t["done_at"]))
            for t in seed["todos"]
        ],
        meetings=[
            Meeting(
                id=m["id"], team_id=m["team_id"], started_at=_ts(m["started_at"]), ended_at=_ts(m["ended_at"]),
                attendee_ids=tuple(m["attendee_ids"]), ratings={r["user_id"]: r["rating"] for r in m["ratings"]},
            )
            for m in seed["meetings"]
        ],
    )
