"""Database schema. Facts are stored; statuses are computed by app.rules (never stored).

Every rule the database can hold, it holds:
- one score per cell, one unsolved Issue per source cell, one live meeting per team;
- owners are real users (FK); "Admin, or a non-Observer member of the team" spans tables, so the API guard checks it;
- only attendees can rate (composite FK to meeting_attendees);
- enums, ranges, Monday weeks, solved ⇔ note, source both-or-neither, goal shape.

Seed ids (u1, m1, t12 …) are the primary keys; new rows get a prefixed generated id.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    and_, or_,
    CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric,
    PrimaryKeyConstraint, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("admin", "team_member", "observer")
GOAL_TYPES = ("at_least", "at_most", "between", "exactly")
ROCK_STATUSES = ("on_track", "off_track", "complete")
SECTIONS = ("scorecard", "ids", "conclude")


def open_as_of(at):
    """SQL twin of engine.is_unsolved: the Issue exists by `at` and isn't solved by then ("as of now")."""
    return and_(Issue.created_at <= at, or_(Issue.solved_at.is_(None), Issue.solved_at > at))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _monday(column: str) -> str:
    return f"EXTRACT(ISODOW FROM {column}) = 1"


Id = String(64)
Ts = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="user_role_valid"),)
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(String(16))


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (PrimaryKeyConstraint("team_id", "user_id"), Index("ix_memberships_user", "user_id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


def _owner_fk() -> ForeignKeyConstraint:
    """Owners are users. Who may own in a team (Admins anywhere, Team Members in their teams) is checked by the API."""
    return ForeignKeyConstraint(["owner_id"], ["users.id"])


class Measurable(Base):
    __tablename__ = "measurables"
    __table_args__ = (
        _owner_fk(),
        UniqueConstraint("id", "team_id", name="uq_measurables_id_team"),  # target of same-team FKs
        Index("ix_measurables_team", "team_id"),
        CheckConstraint(_in("goal_type", GOAL_TYPES), name="goal_type_valid"),
        CheckConstraint(
            "(goal_type = 'between' AND goal_min IS NOT NULL AND goal_max IS NOT NULL"
            " AND goal_min <= goal_max AND goal_value IS NULL)"
            " OR (goal_type <> 'between' AND goal_value IS NOT NULL AND goal_min IS NULL AND goal_max IS NULL)",
            name="goal_shape_matches_type",
        ),
    )
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(Id)
    unit: Mapped[str] = mapped_column(Text)
    goal_type: Mapped[str] = mapped_column(String(16))
    goal_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    goal_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    goal_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        PrimaryKeyConstraint("measurable_id", "week_start"),  # one score per cell; editing is an upsert
        CheckConstraint(_monday("week_start"), name="score_week_is_monday"),
        CheckConstraint("version >= 1", name="scores_version_positive"),
    )
    measurable_id: Mapped[str] = mapped_column(ForeignKey("measurables.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    updated_at: Mapped[datetime | None] = mapped_column(Ts)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))  # bumped by every write (lost-update check)


class Rock(Base):
    __tablename__ = "rocks"
    __table_args__ = (
        _owner_fk(),
        CheckConstraint(_in("status", ROCK_STATUSES), name="rock_status_valid"),
        CheckConstraint(r"quarter ~ '^\d{4}-Q[1-4]$'", name="rock_quarter_format"),
        Index("ix_rocks_team", "team_id"),
        UniqueConstraint("id", "team_id", name="uq_rocks_id_team"),  # target of same-team FKs
    )
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    title: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(Id)
    quarter: Mapped[str] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(16))  # the seeded status; displayed status is computed


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (Index("ix_milestones_rock", "rock_id"),)
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    rock_id: Mapped[str] = mapped_column(ForeignKey("rocks.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(server_default=text("false"))


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        _owner_fk(),
        ForeignKeyConstraint(["source_measurable_id", "team_id"], ["measurables.id", "measurables.team_id"],
                             name="issues_source_measurable_same_team_fkey"),
        UniqueConstraint("id", "team_id", name="uq_issues_id_team"),
        CheckConstraint("solved_at IS NULL OR solved_at >= created_at", name="issue_solved_after_created"),
        CheckConstraint("version >= 1", name="issues_version_positive"),
        CheckConstraint("length(btrim(title)) > 0", name="issue_title_present"),
        CheckConstraint("priority IS NULL OR priority BETWEEN 1 AND 5", name="issue_priority_range"),
        CheckConstraint(
            "(solved_at IS NULL AND resolution_note IS NULL)"
            " OR (solved_at IS NOT NULL AND resolution_note IS NOT NULL AND length(btrim(resolution_note)) > 0)",
            name="issue_solved_iff_note",
        ),
        CheckConstraint(
            "(source_measurable_id IS NULL) = (source_week IS NULL)", name="issue_source_both_or_neither"
        ),
        CheckConstraint(f"source_week IS NULL OR {_monday('source_week')}", name="issue_source_week_is_monday"),
        # No duplicate Issues: one unsolved Issue per Scorecard cell, decided by the database.
        Index(
            "uq_issue_open_per_source_cell", "source_measurable_id", "source_week",
            unique=True, postgresql_where=text("solved_at IS NULL AND source_measurable_id IS NOT NULL"),
        ),
        Index("ix_issues_team", "team_id"),
        Index("ix_issues_source_measurable", "source_measurable_id", "team_id"),
        ForeignKeyConstraint(["source_rock_id", "team_id"], ["rocks.id", "rocks.team_id"],
                             name="issues_source_rock_same_team_fkey"),
        ForeignKeyConstraint(["source_todo_id", "team_id"], ["todos.id", "todos.team_id"],
                             name="issues_source_todo_same_team_fkey"),
        CheckConstraint("(source_measurable_id IS NOT NULL)::int + (source_rock_id IS NOT NULL)::int"
                        " + (source_todo_id IS NOT NULL)::int <= 1", name="issue_at_most_one_source"),
        # Bonus: one unsolved Issue per Rock and per To-Do, decided by the database like Scorecard cells.
        Index("uq_issue_open_per_source_rock", "source_rock_id", "team_id", unique=True,
              postgresql_where=text("solved_at IS NULL AND source_rock_id IS NOT NULL")),
        Index("uq_issue_open_per_source_todo", "source_todo_id", "team_id", unique=True,
              postgresql_where=text("solved_at IS NULL AND source_todo_id IS NOT NULL")),
        Index("ix_issues_source_rock", "source_rock_id", "team_id"),
        Index("ix_issues_source_todo", "source_todo_id", "team_id"),
    )
    id: Mapped[str] = mapped_column(Id, primary_key=True, default=lambda: new_id("i"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    title: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(Id)
    priority: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Ts)
    solved_at: Mapped[datetime | None] = mapped_column(Ts)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    source_measurable_id: Mapped[str | None] = mapped_column(Id)
    source_week: Mapped[date | None] = mapped_column(Date)
    source_rock_id: Mapped[str | None] = mapped_column(Id)
    source_todo_id: Mapped[str | None] = mapped_column(Id)
    updated_at: Mapped[datetime | None] = mapped_column(Ts)  # last app write (null for untouched seed rows)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))  # bumped by every write (lost-update check)


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (
        _owner_fk(),
        CheckConstraint("length(btrim(title)) > 0", name="todo_title_present"),
        CheckConstraint("done_at IS NULL OR done_at >= created_at", name="todo_done_after_created"),
        ForeignKeyConstraint(["source_issue_id", "team_id"], ["issues.id", "issues.team_id"],
                             name="todos_source_issue_same_team_fkey", use_alter=True),
        UniqueConstraint("id", "team_id", name="uq_todos_id_team"),  # target of same-team FKs
        Index("ix_todo_team_due", "team_id", "due_date"),
    )
    id: Mapped[str] = mapped_column(Id, primary_key=True, default=lambda: new_id("t"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    title: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(Id)
    due_date: Mapped[date] = mapped_column(Date)
    done_at: Mapped[datetime | None] = mapped_column(Ts)
    created_at: Mapped[datetime] = mapped_column(Ts)
    source_issue_id: Mapped[str | None] = mapped_column(Id)
    updated_at: Mapped[datetime | None] = mapped_column(Ts)  # last app write (null for untouched seed rows)


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(_in("section", SECTIONS), name="meeting_section_valid"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="meeting_ends_after_start"),
        # One active meeting per team; two simultaneous Starts → one wins, the other joins it.
        Index("uq_meeting_active_per_team", "team_id", unique=True, postgresql_where=text("ended_at IS NULL")),
        Index("ix_meetings_team", "team_id", "started_at"),
    )
    id: Mapped[str] = mapped_column(Id, primary_key=True, default=lambda: new_id("mt"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    facilitator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(Ts)
    ended_at: Mapped[datetime | None] = mapped_column(Ts)
    section: Mapped[str] = mapped_column(String(16), server_default=text("'scorecard'"))
    section_started_at: Mapped[datetime] = mapped_column(Ts)


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"
    __table_args__ = (PrimaryKeyConstraint("meeting_id", "user_id"),)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[datetime | None] = mapped_column(Ts)


class MeetingRating(Base):
    __tablename__ = "meeting_ratings"
    __table_args__ = (
        PrimaryKeyConstraint("meeting_id", "user_id"),  # one rating per person; changing it is an update
        ForeignKeyConstraint(  # only attendees rate
            ["meeting_id", "user_id"], ["meeting_attendees.meeting_id", "meeting_attendees.user_id"], ondelete="CASCADE"
        ),
        CheckConstraint("rating BETWEEN 1 AND 10", name="rating_range"),
    )
    meeting_id: Mapped[str] = mapped_column(Id)
    user_id: Mapped[str] = mapped_column(Id)
    rating: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(Ts)  # last app write (null for seed ratings)
