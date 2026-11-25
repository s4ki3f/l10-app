"""Load seed.json into Postgres. Safe to rerun.

Every seed row is an upsert keyed by its seed id (INSERT … ON CONFLICT DO UPDATE), so:
- running it twice changes nothing;
- running it after edits restores the seed's version of seed records;
- rows created in the app (generated ids) are left alone.

    python -m app.db.seed [path/to/seed.json]
"""
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from ..rules.weeks import parse_utc
from .models import (
    Company, Issue, Measurable, Meeting, MeetingAttendee, MeetingRating, Membership, Milestone, Rock, Score, Team,
    Todo, User,
)
from .session import get_engine

DEFAULT_SEED = Path(__file__).resolve().parents[3] / "seed.json"


def _ts(value):
    return None if value is None else parse_utc(value)


def _d(value):
    return None if value is None else date.fromisoformat(value)


def _n(value):
    return None if value is None else Decimal(str(value))


def _upsert(conn: Connection, model, rows: list[dict]) -> None:
    if not rows:
        return
    table = model.__table__
    keys = [c.name for c in table.primary_key.columns]
    stmt = insert(table).values(rows)
    updates = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
    stmt = stmt.on_conflict_do_update(index_elements=keys, set_=updates) if updates else stmt.on_conflict_do_nothing()
    conn.execute(stmt)


def rows_from_seed(seed: dict) -> list[tuple[type, list[dict]]]:
    """Seed rows in foreign-key order."""
    company = seed["company"]
    goal = lambda g: {"goal_type": g["type"], "goal_value": _n(g.get("value")), "goal_min": _n(g.get("min")), "goal_max": _n(g.get("max"))}  # noqa: E731
    return [
        (Company, [{"id": company["id"], "name": company["name"], "timezone": company["timezone"]}]),
        (Team, [{"id": t["id"], "company_id": company["id"], "name": t["name"]} for t in seed["teams"]]),
        (User, [{**{k: u[k] for k in ("id", "name", "email", "role")}, "company_id": company["id"]} for u in seed["users"]]),
        (Membership, [{"team_id": m["team_id"], "user_id": m["user_id"]} for m in seed["memberships"]]),
        (Measurable, [
            {**{k: m[k] for k in ("id", "team_id", "name", "owner_id", "unit")}, **goal(m["goal"])} for m in seed["measurables"]
        ]),
        (Score, [{"measurable_id": s["measurable_id"], "week_start": _d(s["week_start"]), "value": _n(s["value"])} for s in seed["scores"]]),
        (Rock, [{k: r[k] for k in ("id", "team_id", "title", "owner_id", "quarter", "status")} for r in seed["rocks"]]),
        (Milestone, [{**{k: m[k] for k in ("id", "rock_id", "title", "done")}, "due_date": _d(m["due_date"])} for m in seed["milestones"]]),
        (Issue, [
            {
                **{k: i[k] for k in ("id", "team_id", "title", "owner_id", "priority", "resolution_note")},
                "created_at": _ts(i["created_at"]), "solved_at": _ts(i["solved_at"]),
                "source_measurable_id": (i["source"] or {}).get("measurable_id"),
                "source_week": _d((i["source"] or {}).get("week_start")),
            }
            for i in seed["issues"]
        ]),
        (Todo, [
            {
                **{k: t[k] for k in ("id", "team_id", "title", "owner_id", "source_issue_id")}, "due_date": _d(t["due_date"]),
                "done_at": _ts(t["done_at"]), "created_at": _ts(t["created_at"]),
            }
            for t in seed["todos"]
        ]),
        (Meeting, [
            {
                **{k: m[k] for k in ("id", "team_id", "facilitator_id")},
                "started_at": _ts(m["started_at"]), "ended_at": _ts(m["ended_at"]),
                "section": "conclude", "section_started_at": _ts(m["started_at"]),
            }
            for m in seed["meetings"]
        ]),
        (MeetingAttendee, [{"meeting_id": m["id"], "user_id": u} for m in seed["meetings"] for u in m["attendee_ids"]]),
        (MeetingRating, [
            {"meeting_id": m["id"], "user_id": r["user_id"], "rating": r["rating"]} for m in seed["meetings"] for r in m["ratings"]
        ]),
    ]


def load_seed(conn: Connection, seed: dict) -> None:
    for model, rows in rows_from_seed(seed):
        _upsert(conn, model, rows)


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else DEFAULT_SEED
    seed = json.loads(path.read_text(encoding="utf-8"))
    with get_engine().begin() as conn:
        load_seed(conn, seed)
    print(f"seeded from {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
