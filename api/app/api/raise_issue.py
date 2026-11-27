"""Make it an Issue, for any source: a Scorecard cell, an off-track Rock, an overdue To-Do.

No duplicates, decided by the database: each source kind has a partial unique index allowing one unsolved
Issue. INSERT … ON CONFLICT DO NOTHING; the request that inserts creates it (201), every other request —
including ones at the same instant — gets the winner's Issue (200).
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from ..audit import newly_set, record
from ..db import models as m
from ..realtime import publish
from .deps import assert_valid_owner
from .errors import AppError


@dataclass(frozen=True)
class Source:
    team_id: str
    columns: dict  # the Issue's source columns and their values
    index: tuple[str, ...]  # the partial unique index's columns
    index_where: TextClause  # …and its predicate (must match the index exactly for ON CONFLICT)

    def matches(self):
        return and_(*(getattr(m.Issue, c) == v for c, v in self.columns.items()))


def cell_source(measurable: m.Measurable, week) -> Source:
    return Source(measurable.team_id, {"source_measurable_id": measurable.id, "source_week": week},
                  ("source_measurable_id", "source_week"),
                  text("solved_at IS NULL AND source_measurable_id IS NOT NULL"))


def rock_source(rock: m.Rock) -> Source:
    return Source(rock.team_id, {"source_rock_id": rock.id}, ("source_rock_id", "team_id"),
                  text("solved_at IS NULL AND source_rock_id IS NOT NULL"))


def todo_source(todo: m.Todo) -> Source:
    return Source(todo.team_id, {"source_todo_id": todo.id}, ("source_todo_id", "team_id"),
                  text("solved_at IS NULL AND source_todo_id IS NOT NULL"))


def lock(db: Session, model, *where):
    """SELECT … FOR NO KEY UPDATE, refreshing the identity map: the check that follows reads committed state, and a
    competing write (marking a To-Do done, editing a score) waits until we commit, or we wait for it. NO KEY: rows
    that merely point at this one (a new To-Do on a locked Issue) don't wait — we never change a key."""
    return db.scalars(select(model).where(*where).with_for_update(key_share=True)
                      .execution_options(populate_existing=True)).first()


CELL_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


def cell_lock_key(measurable_id: str, week) -> str:
    return f"l10-cell:{measurable_id}:{week.isoformat()}"


def lock_cell(db: Session, measurable_id: str, week) -> m.Score | None:
    """Every write to one Scorecard cell (saving its score, making it an Issue) takes this lock first, so they
    run one at a time even when the cell has no score row yet — a row lock needs a row. Returns the cell's
    score as committed now (also row-locked, so any other writer of the row waits too)."""
    db.execute(CELL_LOCK, {"key": cell_lock_key(measurable_id, week)})
    return lock(db, m.Score, m.Score.measurable_id == measurable_id, m.Score.week_start == week)


def open_issue(db: Session, src: Source, at: datetime) -> m.Issue | None:
    """The source's unsolved Issue as of `at` (the same rule as the Issues list and the rules report)."""
    return db.scalars(select(m.Issue).where(src.matches(), m.open_as_of(at)).order_by(m.Issue.created_at)).first()


def _index_holder(db: Session, src: Source) -> m.Issue | None:
    """The row the partial unique index says is unsolved (stored truth, used after an ON CONFLICT)."""
    return db.scalars(select(m.Issue).where(src.matches(), m.Issue.solved_at.is_(None))).first()


def raise_or_return(db: Session, src: Source, *, title: str, owner_id: str, at: datetime,
                    check_eligible: Callable[[], None]) -> tuple[m.Issue, bool]:
    """(issue, created). `check_eligible` raises 409 when the source isn't off track / overdue.

    One transaction from the check to the insert, so a lock the caller holds (the cell, the To-Do row) covers both,
    and the retry too. Returning an existing Issue writes nothing, so the transaction ends first: other writers of
    the source don't wait while the response is built.
    """
    existing = open_issue(db, src, at)
    if existing is not None:  # fast path; the insert below is what makes it race-safe
        db.rollback()
        return existing, False
    check_eligible()
    assert_valid_owner(db, src.team_id, owner_id)
    for _attempt in range(2):
        inserted = db.execute(
            insert(m.Issue)
            .values(id=m.new_id("i"), team_id=src.team_id, title=title, owner_id=owner_id, priority=None,
                    created_at=at, updated_at=at, **src.columns)
            .on_conflict_do_nothing(index_elements=list(src.index), index_where=src.index_where)
            .returning(m.Issue.id)
        ).scalar()
        if inserted:
            record(db, team_id=src.team_id, actor_id=owner_id, action="issue.raised", entity_type="issue",
                   entity_id=inserted, label=title, at=at, changes=newly_set(title=title, **src.columns))
            publish(db, src.team_id, "issues")
            db.commit()
            return db.get(m.Issue, inserted), True
        # lost to a concurrent insert. No commit here: under READ COMMITTED the next statement already sees the
        # winner, and the caller's lock stays held, so the eligibility checked above still holds for a retry.
        winner = _index_holder(db, src)
        if winner is not None:
            db.rollback()
            return winner, False
        # the winner was solved between our conflict and our read: the source is free again, try once more
    db.rollback()
    raise AppError(503, "retry", "the Issue changed while it was being created; try again")
