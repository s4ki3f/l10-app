"""The shared dependencies every route goes through: database session, clock, who you are, and the team guard.

Two checks on every team-scoped request, in this order:
1. Can you see this team?   Admin, or a membership in it.       No → 404 (ids don't leak)
2. Can your role do this?   Writes need Admin or Team Member.   No → 403 (you can already see it)
The team always comes from the record being touched, never from the URL alone.
"""
import base64
import hashlib
import hmac
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from fastapi import Cookie, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..db.session import session_factory
from ..rules.clock import Clock
from .errors import forbidden, invalid, not_found, unauthenticated
from .schemas import IdPath

SESSION_COOKIE = "l10_session"
SESSION_MAX_AGE_SECONDS = 12 * 3600  # wall-clock security timer, not business time (APP_NOW)
_DEV_SECRET = "dev-only-secret-change-me"


# ---------- infrastructure ----------

def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def now(clock: Clock = Depends(get_clock)) -> datetime:
    return clock.now()


# ---------- signed session cookie ----------

def _secret() -> bytes:
    return (os.environ.get("SESSION_SECRET") or _DEV_SECRET).encode()  # empty counts as unset (compose passes it through)


def sign_session(user_id: str, issued_at: int | None = None) -> str:
    """Signed `user_id|issued_at`. Stateless: expiry is checked on read; logout clears the browser's copy."""
    issued = int(time.time()) if issued_at is None else issued_at
    payload = base64.urlsafe_b64encode(f"{user_id}|{issued}".encode()).decode()
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def read_session(token: str | None) -> str | None:
    """The user id, or None for anything missing, forged, malformed or expired. Never raises."""
    if not token or "." not in token:
        return None
    payload, mac = token.rsplit(".", 1)
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest().encode()
    try:
        if not hmac.compare_digest(mac.encode("utf-8"), expected):  # bytes: non-ASCII can't raise
            return None
        user_id, issued = base64.urlsafe_b64decode(payload.encode("ascii")).decode().rsplit("|", 1)
        if time.time() - int(issued) > SESSION_MAX_AGE_SECONDS:
            return None
    except (ValueError, TypeError, UnicodeError):
        return None
    return user_id


# ---------- who you are ----------

@dataclass(frozen=True)
class CurrentUser:
    id: str
    name: str
    role: str
    team_ids: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_write(self) -> bool:
        return self.role in ("admin", "team_member")

    def can_see(self, team_id: str) -> bool:
        return self.is_admin or team_id in self.team_ids


def current_user(
    db: Session = Depends(get_db), token: str | None = Cookie(default=None, alias=SESSION_COOKIE)
) -> CurrentUser:
    user_id = read_session(token)
    user = db.get(m.User, user_id) if user_id else None
    if user is None:
        raise unauthenticated()
    teams = db.scalars(select(m.Membership.team_id).where(m.Membership.user_id == user.id)).all()
    return CurrentUser(user.id, user.name, user.role, frozenset(teams))


# ---------- the guard ----------

def authorize(user: CurrentUser, team_id: str, *, write: bool) -> None:
    if not user.can_see(team_id):
        raise not_found("team")
    if write and not user.can_write:
        raise forbidden("observers can read but not change data")


def team_reader(team_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)) -> m.Team:
    """For /teams/{team_id}/… reads."""
    team = db.get(m.Team, team_id)
    if team is None:
        raise not_found("team")
    authorize(user, team.id, write=False)
    return team


def team_writer(team_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)) -> m.Team:
    """For /teams/{team_id}/… writes."""
    team = team_reader(team_id, user, db)
    authorize(user, team.id, write=True)
    return team


def load_in_team(db: Session, model, record_id, user: CurrentUser, *, write: bool, what: str):
    """Load a record, then authorize against the record's own team. Missing and invisible look the same."""
    record = db.get(model, record_id)
    if record is None or not user.can_see(record.team_id):
        raise not_found(what)
    authorize(user, record.team_id, write=write)
    return record


def assert_valid_owner(db: Session, team_id: str, owner_id: str) -> m.User:
    """Owners: any Admin (Admins can do everything a Team Member can, in every team), or a Team Member of this team.
    Observers own nothing. This spans users and memberships, so it lives here rather than in a CHECK."""
    owner = db.get(m.User, owner_id)
    if owner is not None and owner.role == "admin":
        return owner
    member = owner is not None and db.get(m.Membership, {"team_id": team_id, "user_id": owner_id}) is not None
    if not member or owner.role != "team_member":
        raise invalid("invalid_owner", "owner must be an Admin, or a Team Member of this team")
    return owner
