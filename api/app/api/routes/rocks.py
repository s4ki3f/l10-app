"""Rocks (read-only, seeded): current quarter, with computed status and progress."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import milestone_of, rock_of
from ...rules import engine
from ...rules.weeks import local_date, quarter_of
from ..deps import get_db, now, team_reader
from ..schemas import MilestoneOut, PersonRef, RockOut

router = APIRouter(tags=["rocks"])


@router.get("/teams/{team_id}/rocks", response_model=list[RockOut],
            summary="Current quarter's Rocks with owner, computed status, progress and Milestones")
def list_rocks(team: m.Team = Depends(team_reader), db: Session = Depends(get_db), at: datetime = Depends(now)):
    today = local_date(at)
    rocks = db.scalars(select(m.Rock).where(m.Rock.team_id == team.id, m.Rock.quarter == quarter_of(today)).order_by(m.Rock.id)).all()
    milestones = db.scalars(select(m.Milestone).where(m.Milestone.rock_id.in_([r.id for r in rocks]))
                            .order_by(m.Milestone.due_date, m.Milestone.id)).all()
    owners = {u.id: u.name for u in db.scalars(select(m.User).where(m.User.id.in_({r.owner_id for r in rocks})))}
    out = []
    for r in rocks:
        ms = [x for x in milestones if x.rock_id == r.id]
        plain = [milestone_of(x) for x in ms]
        out.append(RockOut(
            id=r.id, title=r.title, owner=PersonRef(id=r.owner_id, name=owners[r.owner_id]), quarter=r.quarter,
            status=engine.rock_status(rock_of(r), plain, at), progress_percent=engine.rock_progress(rock_of(r), plain),
            milestones=[MilestoneOut(id=x.id, title=x.title, due_date=x.due_date, done=x.done,
                                     past_due=not x.done and x.due_date < today) for x in ms],
        ))
    return out
