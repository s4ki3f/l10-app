"""Scorecard cells and Make it an Issue.

No duplicate Issues: the database decides, not the code. A partial unique index allows one unsolved
Issue per (measurable, week). We INSERT … ON CONFLICT DO NOTHING; the request that inserts gets 201,
every other request (including ones arriving at the same instant) gets the winner's Issue with 200.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import goal_of
from ...realtime import publish
from ...rules import engine
from ...rules.weeks import scorecard_weeks, week_of
from ..deps import CurrentUser, assert_valid_owner, current_user, get_db, load_in_team, now
from ..errors import AppError, conflict, invalid
from .issues import issues_out
from ..schemas import IssueOut

router = APIRouter(tags=["scorecard"])

OPEN_CELL = text("solved_at IS NULL AND source_measurable_id IS NOT NULL")  # the partial index's predicate


def issue_title(measurable_name: str, week: date) -> str:
    """Matches the seed: 'New deals closed, week of Nov 2'."""
    return f"{measurable_name}, week of {week.strftime('%b')} {week.day}"


def cell_week(week: date, at: datetime) -> date:
    if week.weekday() != 0:
        raise invalid("invalid_week", "week must be a Monday (week_start)")
    if week not in scorecard_weeks(at):
        raise invalid("invalid_week", "week is outside the 13-week Scorecard")
    return week


def cell_status(db: Session, measurable: m.Measurable, week: date, at: datetime) -> str:
    score = db.get(m.Score, {"measurable_id": measurable.id, "week_start": week})
    return engine.measurable_status(goal_of(measurable), score.value if score else None, week, week_of(at))


def _open_issue(db: Session, measurable_id: str, week: date) -> m.Issue | None:
    return db.scalars(select(m.Issue).where(
        m.Issue.source_measurable_id == measurable_id, m.Issue.source_week == week, m.Issue.solved_at.is_(None)
    )).first()


@router.post(
    "/measurables/{measurable_id}/weeks/{week}/issue",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="Make it an Issue: create the Issue for an off-track cell, or return the existing unsolved one",
    responses={200: {"description": "An unsolved Issue already existed for this cell; it is returned"},
               409: {"description": "The cell is not off track"}},
)
def make_it_an_issue(
    response: Response,
    measurable_id: str,
    week: date = Path(description="The week's Monday, e.g. 2026-11-02"),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    measurable = load_in_team(db, m.Measurable, measurable_id, user, write=True, what="measurable")
    week = cell_week(week, at)

    existing = _open_issue(db, measurable.id, week)
    if existing is not None:  # fast path; the insert below is what makes it race-safe
        response.status_code = status.HTTP_200_OK
        return issues_out(db, [existing])[0]

    if cell_status(db, measurable, week, at) != "off_track":
        raise conflict("not_off_track", "only an off-track cell can be made an Issue")
    assert_valid_owner(db, measurable.team_id, user.id)

    for _attempt in range(2):
        new_id = m.new_id("i")
        stmt = (
            insert(m.Issue)
            .values(id=new_id, team_id=measurable.team_id, title=issue_title(measurable.name, week), owner_id=user.id,
                    priority=None, created_at=at, source_measurable_id=measurable.id, source_week=week)
            .on_conflict_do_nothing(index_elements=["source_measurable_id", "source_week"], index_where=OPEN_CELL)
            .returning(m.Issue.id)
        )
        inserted = db.execute(stmt).scalar()
        if inserted:
            publish(db, measurable.team_id, "issues")
        db.commit()
        if inserted:
            return issues_out(db, [db.get(m.Issue, inserted)])[0]
        winner = _open_issue(db, measurable.id, week)
        if winner is not None:
            response.status_code = status.HTTP_200_OK
            return issues_out(db, [winner])[0]
        # the winner was solved between our conflict and our read: the cell is free again, try once more
    raise AppError(503, "retry", "the Issue changed while it was being created; try again")
