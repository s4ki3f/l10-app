"""Who you are and the teams you can open. Every team-scoped route goes through the same guard."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ..deps import CurrentUser, current_user, get_db, team_reader
from ..schemas import IdPath, MeOut, TeamOut, UserOut

router = APIRouter(tags=["teams"])


@router.get("/me", response_model=MeOut, summary="Who you are and which teams you can open")
def me(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(m.Team).order_by(m.Team.name)
    if not user.is_admin:
        q = q.where(m.Team.id.in_(user.team_ids))
    return MeOut(
        user=UserOut(id=user.id, name=user.name, role=user.role),
        teams=[TeamOut(id=t.id, name=t.name) for t in db.scalars(q)],
        can_write=user.can_write,
    )


@router.get("/teams/{team_id}", response_model=TeamOut, summary="One team you can see (404 otherwise)")
def get_team(team_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    team = team_reader(team_id, user, db)
    return TeamOut(id=team.id, name=team.name)


@router.get("/teams/{team_id}/members", response_model=list[UserOut],
            summary="The team's members plus every Admin (Admins act in every team). Owner pickers drop Observers.")
def team_members(team_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    team = team_reader(team_id, user, db)
    in_team = select(m.Membership.user_id).where(m.Membership.team_id == team.id)
    return db.scalars(
        select(m.User).where((m.User.id.in_(in_team)) | (m.User.role == "admin")).order_by(m.User.name)
    ).all()
