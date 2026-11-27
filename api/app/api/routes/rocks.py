"""Rocks (read-only, seeded): current quarter, with computed status and progress."""
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import milestone_of, rock_of
from ...rules import engine
from ...rules.weeks import local_date, quarter_of
from ..deps import CurrentUser, current_user, get_db, load_in_team, now, team_reader
from ..errors import conflict
from ..raise_issue import raise_or_return, rock_source
from ..schemas import IdPath, IssueOut, MilestoneOut, PersonRef, RockOut
from .issues import issues_out

router = APIRouter(tags=["rocks"])


@router.get("/teams/{team_id}/rocks", response_model=list[RockOut],
            summary="Current quarter's Rocks with owner, computed status, progress and Milestones")
def list_rocks(team: m.Team = Depends(team_reader), db: Session = Depends(get_db), at: datetime = Depends(now)):
    today = local_date(at)
    rocks = db.scalars(select(m.Rock).where(m.Rock.team_id == team.id, m.Rock.quarter == quarter_of(today)).order_by(m.Rock.id)).all()
    milestones = db.scalars(select(m.Milestone).where(m.Milestone.rock_id.in_([r.id for r in rocks]))
                            .order_by(m.Milestone.due_date, m.Milestone.id)).all()
    owners = {u.id: u.name for u in db.scalars(select(m.User).where(m.User.id.in_({r.owner_id for r in rocks})))}
    raised = {i.source_rock_id: i.id for i in db.scalars(select(m.Issue).where(
        m.Issue.source_rock_id.in_([r.id for r in rocks]), m.open_as_of(at)))} if rocks else {}
    out = []
    for r in rocks:
        ms = [x for x in milestones if x.rock_id == r.id]
        plain = [milestone_of(x) for x in ms]
        out.append(RockOut(
            id=r.id, title=r.title, owner=PersonRef(id=r.owner_id, name=owners[r.owner_id]), quarter=r.quarter,
            status=engine.rock_status(rock_of(r), plain, at), progress_percent=engine.rock_progress(rock_of(r), plain),
            milestones=[MilestoneOut(id=x.id, title=x.title, due_date=x.due_date, done=x.done,
                                     past_due=not x.done and x.due_date < today) for x in ms],
            open_issue_id=raised.get(r.id),
        ))
    return out


@router.post("/rocks/{rock_id}/issue", response_model=IssueOut, status_code=status.HTTP_201_CREATED,
             summary="Bonus — Make it an Issue from an off-track Rock, or return its existing unsolved Issue (200)",
             responses={200: {"description": "An unsolved Issue already existed for this Rock; it is returned"},
                        409: {"description": "The Rock is not off track"}})
def issue_from_rock(response: Response, rock_id: IdPath, user: CurrentUser = Depends(current_user),
                    db: Session = Depends(get_db), at: datetime = Depends(now)):
    rock = load_in_team(db, m.Rock, rock_id, user, write=True, what="rock")

    def off_track() -> None:
        if rock.quarter != quarter_of(local_date(at)):  # same scope as the Rocks list: this quarter only
            raise conflict("not_current_quarter", "only this quarter's Rocks can be made an Issue")
        milestones = [milestone_of(x) for x in db.scalars(select(m.Milestone).where(m.Milestone.rock_id == rock.id))]
        if engine.rock_status(rock_of(rock), milestones, at) != "off_track":
            raise conflict("not_off_track", "only an off-track Rock can be made an Issue")

    issue, created = raise_or_return(db, rock_source(rock), title=f"Rock off track: {rock.title}",
                                     owner_id=user.id, at=at, check_eligible=off_track)
    if not created:
        response.status_code = status.HTTP_200_OK
    return issues_out(db, [issue])[0]
