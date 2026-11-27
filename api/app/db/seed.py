"""Load seed.json into Postgres. Safe to rerun.

`--reset` first deletes everything the app created (rows whose ids are not in the seed, and score cells
the seed doesn't have), so the database is exactly the seed again. The E2E tests start from it.

Every seed row is an upsert keyed by its seed id (INSERT … ON CONFLICT DO UPDATE), so:
- running it twice changes nothing;
- running it after edits restores the seed's version of seed records;
- rows created in the app (generated ids) are left alone.

    python -m app.db.seed [--reset] [path/to/seed.json]
"""
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, delete, tuple_, update
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


# On a plain rerun, an Issue's solved state belongs to the app: reopening a seeded Issue the team has since
# solved could collide with a newer open Issue on the same cell (one unsolved Issue per cell). --reset restores it.
KEEP_ON_RERUN = {
    Issue: {"solved_at", "resolution_note", "updated_at", "version"},
    Todo: {"updated_at"},
    Score: {"updated_at", "updated_by", "version"},
    MeetingRating: {"updated_at"},
}


def _upsert(conn: Connection, model, rows: list[dict], keep: set[str] = frozenset()) -> None:
    if not rows:
        return
    table = model.__table__
    keys = [c.name for c in table.primary_key.columns]
    stmt = insert(table).values(rows)
    updates = {c: stmt.excluded[c] for c in rows[0] if c not in keys and c not in keep}
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
        (Score, [{"measurable_id": s["measurable_id"], "week_start": _d(s["week_start"]), "value": _n(s["value"]), "updated_at": None, "updated_by": None,
                  "version": 1} for s in seed["scores"]]),
        (Rock, [{k: r[k] for k in ("id", "team_id", "title", "owner_id", "quarter", "status")} for r in seed["rocks"]]),
        (Milestone, [{**{k: m[k] for k in ("id", "rock_id", "title", "done")}, "due_date": _d(m["due_date"])} for m in seed["milestones"]]),
        (Issue, [
            {
                **{k: i[k] for k in ("id", "team_id", "title", "owner_id", "priority", "resolution_note")},
                "created_at": _ts(i["created_at"]), "solved_at": _ts(i["solved_at"]),
                "source_measurable_id": (i["source"] or {}).get("measurable_id"),
                "source_week": _d((i["source"] or {}).get("week_start")), "updated_at": None, "version": 1,
            }
            for i in seed["issues"]
        ]),
        (Todo, [
            {
                **{k: t[k] for k in ("id", "team_id", "title", "owner_id", "source_issue_id")}, "due_date": _d(t["due_date"]),
                "done_at": _ts(t["done_at"]), "created_at": _ts(t["created_at"]), "updated_at": None,
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
            {"meeting_id": m["id"], "user_id": r["user_id"], "rating": r["rating"], "updated_at": None}
            for m in seed["meetings"] for r in m["ratings"]
        ]),
    ]


def load_seed(conn: Connection, seed: dict, *, exact: bool = False) -> None:
    """Upsert every seed row. `exact` (used by --reset) also restores fields the app owns on a rerun."""
    for model, rows in rows_from_seed(seed):
        _upsert(conn, model, rows, keep=set() if exact else KEEP_ON_RERUN.get(model, set()))


def reset_to_seed(conn: Connection, seed: dict) -> None:
    """Remove app-created rows (children first), then load the seed."""
    ids = lambda key: [x["id"] for x in seed[key]]  # noqa: E731
    conn.execute(delete(Meeting).where(Meeting.id.not_in(ids("meetings"))))  # attendees/ratings cascade
    conn.execute(delete(MeetingRating))  # seeded ratings are re-inserted below; this drops ratings added later
    conn.execute(delete(MeetingAttendee).where(MeetingAttendee.meeting_id.in_(ids("meetings"))))
    # Issues and To-Dos point at each other (a To-Do from an Issue, an Issue from a To-Do): cut the
    # Issue→To-Do links to app To-Dos first, then delete To-Dos, then Issues.
    conn.execute(update(Issue).where(Issue.source_todo_id.not_in(ids("todos"))).values(source_todo_id=None))
    conn.execute(delete(Todo).where(Todo.id.not_in(ids("todos"))))
    conn.execute(delete(Issue).where(Issue.id.not_in(ids("issues"))))
    seeded_cells = [(s["measurable_id"], date.fromisoformat(s["week_start"])) for s in seed["scores"]]
    conn.execute(delete(Score).where(tuple_(Score.measurable_id, Score.week_start).not_in(seeded_cells)))
    load_seed(conn, seed, exact=True)


def main(argv: list[str]) -> int:
    reset = "--reset" in argv
    rest = [a for a in argv if a != "--reset"]
    path = Path(rest[0]) if rest else DEFAULT_SEED
    seed = json.loads(path.read_text(encoding="utf-8"))
    with get_engine().begin() as conn:
        reset_to_seed(conn, seed) if reset else load_seed(conn, seed)
    print(f"{'reset to' if reset else 'seeded from'} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
