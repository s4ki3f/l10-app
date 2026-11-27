"""Request and response models. Inputs are validated here, at the edge; the DB constraints back them up."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Ids are seed ids (u1, m1, t12) or generated (i_3f…): letters, digits, "_" and "-". Rejecting anything else at
# the edge keeps odd bytes (e.g. NUL, which the database driver refuses) from ever reaching a query.
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
IdPath = Annotated[str, Path(pattern=ID_PATTERN, description="Record id, e.g. team_lead, i5, mt_3f2a…")]
Id = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
_TEXT = r"^[^\x00]*$"  # any text except NUL
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=_TEXT)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=_TEXT)]
Priority = Annotated[int, Field(ge=1, le=5, strict=True)]  # strict: true / "3" are client bugs, not priorities


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- errors (the one shape every 4xx/5xx uses) ----------

class FieldError(BaseModel):
    field: str
    message: str


class ErrorDetail(BaseModel):
    code: str = Field(description="Machine-readable: not_signed_in, forbidden, not_found, invalid_input, "
                                  "stale_write, issue_solved, not_off_track, meeting_ended, …")
    message: str
    fields: list[FieldError] | None = Field(None, description="On invalid_input: which fields and why")
    current: dict | None = Field(None, description="On stale_write: what is stored now")


class ErrorOut(BaseModel):
    error: ErrorDetail


# ---------- auth ----------

class UserOut(BaseModel):
    id: str
    name: str
    role: Literal["admin", "team_member", "observer"]


class LoginIn(Strict):
    user_id: Id


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


class ItemRef(BaseModel):
    """A link to an Issue or To-Do."""
    id: str
    title: str


class IssueSource(BaseModel):
    measurable_id: str
    measurable_name: str
    week_start: date


class IssueOut(BaseModel):
    id: str
    version: int = Field(description="Send back as expected_version to change priority without overwriting someone else")
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
    owner_id: Id


class TodoOut(BaseModel):
    id: str
    team_id: str
    title: str
    owner: PersonRef
    due_date: date
    done_at: datetime | None
    created_at: datetime
    overdue: bool
    source_issue: ItemRef | None = Field(None, description="The Issue this To-Do came from")


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
    rating: Annotated[int, Field(ge=1, le=10, strict=True)]


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
    issues_solved: list[ItemRef]
    todos_created: list[ItemRef]
    attendees: list[AttendeeOut]
    rating: float | None


# ---------- scorecard ----------

class GoalOut(BaseModel):
    type: Literal["at_least", "at_most", "between", "exactly"]
    value: float | None
    min: float | None
    max: float | None


class CellOut(BaseModel):
    week_start: date
    version: int = Field(description="0 when there's no score yet; send back as expected_version")
    value: float | None
    status: Literal["on_track", "off_track", "pending"]
    open_issue_id: str | None = Field(description="The unsolved Issue made from this cell, if any")


class ScorecardRow(BaseModel):
    id: str
    name: str
    unit: str
    owner: PersonRef
    goal: GoalOut
    cells: list[CellOut]


class ScorecardOut(BaseModel):
    weeks: list[date] = Field(description="13 weeks, oldest first, ending with the current week")
    current_week: date
    last_closed_week: date
    rows: list[ScorecardRow]


Score = Annotated[Decimal, Field(max_digits=14, decimal_places=4, allow_inf_nan=False)]


ExpectedVersion = Annotated[int, Field(ge=0, strict=True, description=(
    "Lost-update protection: the version you saw (0 = no score yet). A mismatch returns 409 stale_write "
    "with the current value. Omit for last-save-wins."))]


class ScoreIn(Strict):
    value: Score
    expected_version: ExpectedVersion | None = None


# ---------- issues: changes ----------

class IssuePatch(Strict):
    priority: Priority | None = Field(description="1–5, or null to clear")
    expected_version: ExpectedVersion | None = None


class SolveIn(Strict):
    note: Note


# ---------- to-dos ----------

class CompletionOut(BaseModel):
    week_start: date
    due: int
    done: int
    rate_percent: int
    meets_target: bool


class TodoList(BaseModel):
    items: list[TodoOut]
    last_closed_week: date
    completion: CompletionOut | None = Field(description="Last closed week's completion rate; null when nothing was due")


# ---------- rocks ----------

class MilestoneOut(BaseModel):
    id: str
    title: str
    due_date: date
    done: bool
    past_due: bool


class RockOut(BaseModel):
    id: str
    title: str
    owner: PersonRef
    quarter: str
    status: Literal["on_track", "off_track", "complete"]
    progress_percent: int
    milestones: list[MilestoneOut]
