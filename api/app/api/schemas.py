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


# ---------- meetings ----------

Section = Literal["scorecard", "ids", "conclude"]


class AgendaItem(BaseModel):
    key: Section
    title: str
    minutes: int


class AttendeeOut(BaseModel):
    id: str
    name: str
    role: str


class RatingOut(BaseModel):
    user_id: str
    rating: int


class MeetingState(BaseModel):
    """Everything a browser needs to render the meeting. The timer is derived, never counted down:
    elapsed = server_now - section_started_at. Clients add their own clock offset from server_now."""
    id: str
    team_id: str
    facilitator: PersonRef
    started_at: datetime
    ended_at: datetime | None
    section: Section
    section_started_at: datetime
    server_now: datetime
    elapsed_seconds: int
    section_limit_seconds: int
    over_time: bool
    agenda: list[AgendaItem]
    attendees: list[AttendeeOut]
    ratings: list[RatingOut]
    average_rating: float | None
    new_todos: list[TodoOut] = Field(description="To-Dos created since the meeting started (shown in Conclude)")


class ActiveMeetingOut(BaseModel):
    meeting: MeetingState | None
    server_now: datetime


class AdvanceIn(Strict):
    from_section: Section = Field(description="The section you are looking at; a stale click (double-click, two tabs) gets 409")


class RatingIn(Strict):
    rating: Annotated[int, Field(ge=1, le=10)]


class SummaryMeasurable(BaseModel):
    id: str
    name: str
    status: Literal["on_track", "off_track", "pending"]


class SummaryOut(BaseModel):
    meeting_id: str
    team_id: str
    started_at: datetime
    ended_at: datetime
    scorecard_week: date
    measurable_status: list[SummaryMeasurable]
    issues_solved: list[PersonRef] = Field(description="id and title")
    todos_created: list[PersonRef] = Field(description="id and title")
    attendees: list[AttendeeOut]
    rating: float | None
