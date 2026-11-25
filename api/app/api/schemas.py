"""Request and response models. Inputs are validated here, at the edge; the DB constraints back them up."""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Priority = Annotated[int, Field(ge=1, le=5)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- auth ----------

class UserOut(BaseModel):
    id: str
    name: str
    role: Literal["admin", "team_member", "observer"]


class LoginIn(Strict):
    user_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class TeamOut(BaseModel):
    id: str
    name: str


class MeOut(BaseModel):
    user: UserOut
    teams: list[TeamOut] = Field(description="Teams you can open: every team for Admins, your own teams otherwise")
    can_write: bool


# ---------- issues ----------

class PersonRef(BaseModel):
    id: str
    name: str


class IssueSource(BaseModel):
    measurable_id: str
    measurable_name: str
    week_start: date


class IssueOut(BaseModel):
    id: str
    team_id: str
    title: str
    owner: PersonRef
    priority: int | None
    created_at: datetime
    solved_at: datetime | None
    resolution_note: str | None
    source: IssueSource | None


class IssueCreate(Strict):
    title: Title
    priority: Priority | None = None


class TodoCreate(Strict):
    title: Title
    owner_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class TodoOut(BaseModel):
    id: str
    team_id: str
    title: str
    owner: PersonRef
    due_date: date
    done_at: datetime | None
    created_at: datetime
    overdue: bool
    source_issue: PersonRef | None = Field(None, description="The Issue this To-Do came from (id and title)")
