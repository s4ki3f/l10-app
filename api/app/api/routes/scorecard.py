"""Scorecard cells and Make it an Issue.

No duplicate Issues: the database decides, not the code. A partial unique index allows one unsolved
Issue per (measurable, week). We INSERT … ON CONFLICT DO NOTHING; the request that inserts gets 201,
every other request (including ones arriving at the same instant) gets the winner's Issue with 200.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...audit import record, score_entity_id
from ...db import models as m
from ...db.load import goal_of
from ...realtime import publish
from ...rules import engine
from ...rules.weeks import last_closed_week, scorecard_weeks, week_of
from ..deps import CurrentUser, current_user, get_db, load_in_team, now, team_reader
from ..errors import AppError, conflict, invalid
from ..raise_issue import cell_source, lock_cell, open_issue, raise_or_return
from .issues import issues_out
from ..schemas import CellOut, GoalOut, IdPath, IssueOut, PersonRef, ScoreIn, ScorecardOut, ScorecardRow

router = APIRouter(tags=["scorecard"])


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
    # take the cell's lock so a concurrent score edit — or a first score on an empty cell — can't make it
    # on track between our check and our insert
    lock_cell(db, measurable.id, week)

    def off_track() -> None:
        if cell_status(db, measurable, week, at) != "off_track":
            raise conflict("not_off_track", "only an off-track cell can be made an Issue")

    issue, created = raise_or_return(db, cell_source(measurable, week), title=issue_title(measurable.name, week),
                                     owner_id=user.id, at=at, check_eligible=off_track)
    if not created:
        response.status_code = status.HTTP_200_OK
    return issues_out(db, [issue])[0]


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
    before = lock_cell(db, measurable.id, week)  # writers of this cell queue here, so `before` is exact
    old = None if before is None else before.value
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
    record(db, team_id=measurable.team_id, actor_id=user.id, action="score.set", entity_type="score",
           entity_id=score_entity_id(measurable.id, week), label=issue_title(measurable.name, week), at=at,
           changes={"value": [old, body.value]})
    publish(db, measurable.team_id, "scorecard")
    db.commit()
    issue = open_issue(db, cell_source(measurable, week), at)
    return CellOut(week_start=week, version=version, value=float(body.value),
                   status=engine.measurable_status(goal_of(measurable), body.value, week, week_of(at)),
                   open_issue_id=issue.id if issue else None)
