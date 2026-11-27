"""Read the rules engine's Dataset from Postgres (used by the rules report and the API)."""
from collections import defaultdict

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from ..rules import engine as r
from ..rules.dataset import Dataset
from . import models as m
from .session import session_factory


def goal_of(row: m.Measurable) -> r.Goal:
    return r.Goal(type=row.goal_type, value=row.goal_value, min=row.goal_min, max=row.goal_max)


def measurable_of(row: m.Measurable) -> r.Measurable:
    return r.Measurable(row.id, row.team_id, row.name, goal_of(row))


def todo_of(row: m.Todo) -> r.Todo:
    return r.Todo(row.id, row.team_id, row.due_date, row.created_at, row.done_at)


def issue_of(row: m.Issue) -> r.Issue:
    return r.Issue(row.id, row.team_id, row.created_at, row.priority, row.solved_at, row.source_measurable_id, row.source_week)


def rock_of(row: m.Rock) -> r.Rock:
    return r.Rock(row.id, row.team_id, row.quarter, row.status)


def milestone_of(row: m.Milestone) -> r.Milestone:
    return r.Milestone(row.id, row.rock_id, row.due_date, row.done)


def load_dataset(session: Session, team_ids: list[str] | None = None) -> Dataset:
    """Everything the rules need; optionally only for some teams."""
    def scoped(model):
        q = select(model)
        return q.where(model.team_id.in_(team_ids)) if team_ids is not None else q

    teams = session.scalars(select(m.Team.id).order_by(m.Team.id)).all()
    measurables = session.scalars(scoped(m.Measurable)).all()
    scores = session.execute(
        select(m.Score.measurable_id, m.Score.week_start, m.Score.value)
        .where(m.Score.measurable_id.in_([x.id for x in measurables]))
    ).all()
    rocks = session.scalars(scoped(m.Rock)).all()
    milestones = session.scalars(select(m.Milestone).where(m.Milestone.rock_id.in_([x.id for x in rocks]))).all()
    meetings = session.scalars(scoped(m.Meeting)).all()
    ids = [x.id for x in meetings]
    attendees, ratings = defaultdict(list), defaultdict(dict)
    for a in session.scalars(select(m.MeetingAttendee).where(m.MeetingAttendee.meeting_id.in_(ids))):
        attendees[a.meeting_id].append(a.user_id)
    for x in session.scalars(select(m.MeetingRating).where(m.MeetingRating.meeting_id.in_(ids))):
        ratings[x.meeting_id][x.user_id] = x.rating

    return Dataset(
        team_ids=[t for t in teams if team_ids is None or t in team_ids],
        measurables=[measurable_of(x) for x in measurables],
        scores={(s.measurable_id, s.week_start): s.value for s in scores},
        rocks=[rock_of(x) for x in rocks],
        milestones=[milestone_of(x) for x in milestones],
        issues=[issue_of(x) for x in session.scalars(scoped(m.Issue))],
        todos=[todo_of(x) for x in session.scalars(scoped(m.Todo))],
        meetings=[
            r.Meeting(x.id, x.team_id, x.started_at, x.ended_at, tuple(attendees[x.id]), ratings[x.id]) for x in meetings
        ],
    )


def last_app_write(session: Session):
    """The latest moment the app itself wrote (seed rows carry no write time). Used to resume the clock."""
    candidates = [
        select(func.max(m.Issue.updated_at)),
        select(func.max(m.Todo.updated_at)),
        select(func.max(m.Score.updated_at)),
        select(func.max(func.greatest(m.Meeting.started_at, m.Meeting.section_started_at, m.Meeting.ended_at)))
        .where(m.Meeting.id.like(literal_column("'mt\\_%'"))),
        select(func.max(m.MeetingAttendee.joined_at)),
        select(func.max(m.MeetingRating.updated_at)),
    ]
    found = [session.scalar(q) for q in candidates]
    return max((f for f in found if f is not None), default=None)


def dataset_from_db() -> Dataset:
    with session_factory()() as session:
        return load_dataset(session)
