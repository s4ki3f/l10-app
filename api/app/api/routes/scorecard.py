"""Scorecard cells and Make it an Issue.

No duplicate Issues: the database decides, not the code. A partial unique index allows one unsolved
Issue per (measurable, week). We INSERT … ON CONFLICT DO NOTHING; the request that inserts gets 201,
every other request (including ones arriving at the same instant) gets the winner's Issue with 200.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import goal_of
from ...realtime import publish
from ...rules import engine
from ...rules.weeks import last_closed_week, scorecard_weeks, week_of
from ..deps import CurrentUser, assert_valid_owner, current_user, get_db, load_in_team, now, team_reader
from ..errors import AppError, conflict, invalid
from .issues import issues_out
from ..schemas import CellOut, GoalOut, IdPath, IssueOut, PersonRef, ScoreIn, ScorecardOut, ScorecardRow

router = APIRouter(tags=["scorecard"])

OPEN_CELL = text("solved_at IS NULL AND source_measurable_id IS NOT NULL")  # the partial index's predicate


def issue_title(measurable_name: str, week: date) -> str:
    """Matches the seed: 'New deals closed, week of Nov 2'."""
    return f"{measurable_name}, week of {week.strftime('%b')} {week.day}"


def _stale_score(db: Session, measurable_id: str, week: date) -> AppError:
    """409 with what's there now, so the client can show who changed it and to what."""
    current = db.get(m.Score, {"measurable_id": measurable_id, "week_start": week})
    by = db.get(m.User, current.updated_by) if current and current.updated_by else None
    return AppError(409, "stale_write", "someone changed this score after you loaded it", current={
        "value": float(current.value) if current else None, "version": current.version if current else 0,
        "updated_by": by.name if by else None})


def cell_week(week: date, at: datetime) -> date:
    if week.weekday() != 0:
        raise invalid("invalid_week", "week must be a Monday (week_start)")
    if week not in scorecard_weeks(at):
        raise invalid("invalid_week", "week is outside the 13-week Scorecard")
    return week


def cell_status(db: Session, measurable: m.Measurable, week: date, at: datetime) -> str:
    score = db.get(m.Score, {"measurable_id": measurable.id, "week_start": week})
    return engine.measurable_status(goal_of(measurable), score.value if score else None, week, week_of(at))


def _open_issue(db: Session, measurable_id: str, week: date, at: datetime) -> m.Issue | None:
    """The cell's unsolved Issue as of `at` (same rule as the Issues list and the rules report)."""
    return db.scalars(select(m.Issue).where(
        m.Issue.source_measurable_id == measurable_id, m.Issue.source_week == week, m.open_as_of(at)
    ).order_by(m.Issue.created_at)).first()


def _index_holder(db: Session, measurable_id: str, week: date) -> m.Issue | None:
    """The row the partial unique index says is unsolved (stored truth, used after an ON CONFLICT)."""
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
    measurable_id: IdPath,
    week: date = Path(description="The week's Monday, e.g. 2026-11-02"),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    measurable = load_in_team(db, m.Measurable, measurable_id, user, write=True, what="measurable")
    week = cell_week(week, at)

    existing = _open_issue(db, measurable.id, week, at)
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
                    priority=None, created_at=at, updated_at=at, source_measurable_id=measurable.id, source_week=week)
            .on_conflict_do_nothing(index_elements=["source_measurable_id", "source_week"], index_where=OPEN_CELL)
            .returning(m.Issue.id)
        )
        inserted = db.execute(stmt).scalar()
        if inserted:
            publish(db, measurable.team_id, "issues")
        db.commit()
        if inserted:
            return issues_out(db, [db.get(m.Issue, inserted)])[0]
        winner = _index_holder(db, measurable.id, week)
        if winner is not None:
            response.status_code = status.HTTP_200_OK
            return issues_out(db, [winner])[0]
        # the winner was solved between our conflict and our read: the cell is free again, try once more
    raise AppError(503, "retry", "the Issue changed while it was being created; try again")


# ---------- reading and editing the Scorecard ----------

def _num(v):
    return None if v is None else float(v)


@router.get("/teams/{team_id}/scorecard", response_model=ScorecardOut,
            summary="Measurables × the last 13 weeks; every cell has its value, computed status and open Issue")
def get_scorecard(team: m.Team = Depends(team_reader), db: Session = Depends(get_db), at: datetime = Depends(now)):
    weeks = scorecard_weeks(at)
    current = week_of(at)
    measurables = db.scalars(select(m.Measurable).where(m.Measurable.team_id == team.id).order_by(m.Measurable.id)).all()
    ids = [x.id for x in measurables]
    rows_ = db.scalars(select(m.Score).where(m.Score.measurable_id.in_(ids), m.Score.week_start.in_(weeks))).all()
    scores = {(s.measurable_id, s.week_start): s.value for s in rows_}
    versions = {(s.measurable_id, s.week_start): s.version for s in rows_}
    open_issues = {(i.source_measurable_id, i.source_week): i.id for i in db.scalars(
        select(m.Issue).where(m.Issue.source_measurable_id.in_(ids), m.open_as_of(at)))}
    owners = {u.id: u.name for u in db.scalars(select(m.User).where(m.User.id.in_({x.owner_id for x in measurables})))}
    rows = []
    for x in measurables:
        goal = goal_of(x)
        rows.append(ScorecardRow(
            id=x.id, name=x.name, unit=x.unit, owner=PersonRef(id=x.owner_id, name=owners[x.owner_id]),
            goal=GoalOut(type=x.goal_type, value=_num(x.goal_value), min=_num(x.goal_min), max=_num(x.goal_max)),
            cells=[CellOut(week_start=w, version=versions.get((x.id, w), 0), value=_num(scores.get((x.id, w))),
                           status=engine.measurable_status(goal, scores.get((x.id, w)), w, current),
                           open_issue_id=open_issues.get((x.id, w)))
                   for w in weeks],
        ))
    return ScorecardOut(weeks=weeks, current_week=current, last_closed_week=last_closed_week(at), rows=rows)


@router.put("/measurables/{measurable_id}/weeks/{week}/score", response_model=CellOut,
            summary="Enter or edit a score (last save wins). Week must be inside the 13-week Scorecard.")
def put_score(
    measurable_id: IdPath,
    body: ScoreIn,
    week: date = Path(description="The week's Monday"),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    measurable = load_in_team(db, m.Measurable, measurable_id, user, write=True, what="measurable")
    week = cell_week(week, at)
    fields = {"value": body.value, "updated_at": at, "updated_by": user.id}
    cell = (m.Score.measurable_id == measurable.id) & (m.Score.week_start == week)
    ev = body.expected_version
    if ev is None:  # last save wins (the brief's core behaviour)
        version = db.execute(insert(m.Score).values(measurable_id=measurable.id, week_start=week, **fields)
                             .on_conflict_do_update(index_elements=["measurable_id", "week_start"],
                                                    set_={**fields, "version": m.Score.version + 1})
                             .returning(m.Score.version)).scalar()
    elif ev == 0:  # "I saw no score": only create it if nobody else did first
        version = db.execute(insert(m.Score).values(measurable_id=measurable.id, week_start=week, **fields)
                             .on_conflict_do_nothing().returning(m.Score.version)).scalar()
    else:  # "I saw version ev": only overwrite that exact version
        version = db.execute(update(m.Score).where(cell, m.Score.version == ev)
                             .values(**fields, version=m.Score.version + 1).returning(m.Score.version)).scalar()
    if version is None:
        db.rollback()
        raise _stale_score(db, measurable.id, week)
    publish(db, measurable.team_id, "scorecard")
    db.commit()
    issue = _open_issue(db, measurable.id, week, at)
    return CellOut(week_start=week, version=version, value=float(body.value),
                   status=engine.measurable_status(goal_of(measurable), body.value, week, week_of(at)),
                   open_issue_id=issue.id if issue else None)
