"""Sign-in via the seeded user switcher (sign-up is out of scope). Identity lives only in the signed cookie."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ..deps import SESSION_COOKIE, CurrentUser, current_user, get_db, sign_session, team_reader
from ..errors import AppError
from ..schemas import LoginIn, MeOut, TeamOut, UserOut

router = APIRouter(tags=["auth"])


@router.get("/auth/users", response_model=list[UserOut], summary="Seeded users for the sign-in switcher")
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(m.User).order_by(m.User.id)).all()


@router.post("/auth/login", response_model=UserOut, summary="Sign in as a seeded user; sets the session cookie")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.get(m.User, body.user_id)
    if user is None:
        raise AppError(401, "unknown_user", "no such user")
    response.set_cookie(SESSION_COOKIE, sign_session(user.id), httponly=True, samesite="lax", path="/")
    return user


@router.post("/auth/logout", status_code=204, summary="Clear the session cookie")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")


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
def get_team(team_id: str, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    team = team_reader(team_id, user, db)
    return TeamOut(id=team.id, name=team.name)


@router.get("/teams/{team_id}/members", response_model=list[UserOut],
            summary="The team's members plus every Admin (Admins act in every team). Owner pickers drop Observers.")
def team_members(team_id: str, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    team = team_reader(team_id, user, db)
    in_team = select(m.Membership.user_id).where(m.Membership.team_id == team.id)
    return db.scalars(
        select(m.User).where((m.User.id.in_(in_team)) | (m.User.role == "admin")).order_by(m.User.name)
    ).all()
