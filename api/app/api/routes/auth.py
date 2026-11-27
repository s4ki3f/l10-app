"""Sign-in via the seeded user switcher (sign-up is out of scope). Identity lives only in the signed cookie."""
import os

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ..deps import SESSION_COOKIE, SESSION_MAX_AGE_SECONDS, get_db, sign_session
from ..errors import AppError
from ..schemas import LoginIn, UserOut

router = APIRouter(tags=["auth"])


@router.get("/auth/users", response_model=list[UserOut], summary="Seeded users for the sign-in switcher")
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(m.User).order_by(m.User.id)).all()


@router.post("/auth/login", response_model=UserOut, summary="Sign in as a seeded user; sets the session cookie")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.get(m.User, body.user_id)
    if user is None:
        raise AppError(401, "unknown_user", "no such user")
    response.set_cookie(SESSION_COOKIE, sign_session(user.id), httponly=True, samesite="lax", path="/",
                        max_age=SESSION_MAX_AGE_SECONDS, secure=os.environ.get("COOKIE_SECURE") == "1")
    return user


@router.post("/auth/logout", status_code=204, summary="Clear the session cookie")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
