"""The Level 10 Meeting. The database owns its state; commands go up over REST, hints come down over SSE.

- One active meeting per team (partial unique index). Two simultaneous Starts: one inserts, the other joins it.
- Only the facilitator advances or ends, at any time. Advance names the section it came from, so a
  double-click or a second tab can't skip a section (409 on a stale click).
- An overrun section is shown as over time and never advances on its own.
- Attending Admins/Team Members rate 1–10 in Conclude and can change it until the end.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import load_dataset
from ...realtime import publish
from ...rules import engine
from ..deps import CurrentUser, current_user, get_db, load_in_team, now, team_reader, team_writer
from ..errors import conflict, forbidden
from ..schemas import (
    ActiveMeetingOut, AdvanceIn, IdPath, ItemRef, AgendaItem, AttendeeOut, MeetingState, PersonRef, RatingIn, RatingOut,
    SummaryMeasurable, SummaryOut,
)
from .issues import todos_out

router = APIRouter(tags=["meetings"])

AGENDA = [AgendaItem(key="scorecard", title="Scorecard", minutes=5),
          AgendaItem(key="ids", title="IDS", minutes=60),
          AgendaItem(key="conclude", title="Conclude", minutes=10)]
ORDER = [a.key for a in AGENDA]
LIMIT = {a.key: a.minutes * 60 for a in AGENDA}
ACTIVE = text("ended_at IS NULL")


def meeting_state(db: Session, mt: m.Meeting, at: datetime) -> MeetingState:
    facilitator = db.get(m.User, mt.facilitator_id)
    attendees = db.execute(
        select(m.User).join(m.MeetingAttendee, m.MeetingAttendee.user_id == m.User.id)
        .where(m.MeetingAttendee.meeting_id == mt.id).order_by(m.User.name)
    ).scalars().all()
    ratings = db.scalars(select(m.MeetingRating).where(m.MeetingRating.meeting_id == mt.id).order_by(m.MeetingRating.user_id)).all()
    average = engine.meeting_rating([r.rating for r in ratings])
    until = mt.ended_at or at
    new_todos = db.scalars(select(m.Todo).where(
        m.Todo.team_id == mt.team_id, m.Todo.created_at >= mt.started_at, m.Todo.created_at <= until
    ).order_by(m.Todo.created_at, m.Todo.id)).all()
    # max(0, …): if the API restarts, the running clock restarts at APP_NOW and could sit before section_started_at
    elapsed = max(0, int(((mt.ended_at or at) - mt.section_started_at).total_seconds()))
    return MeetingState(
        id=mt.id, team_id=mt.team_id, facilitator=PersonRef(id=facilitator.id, name=facilitator.name),
        started_at=mt.started_at, ended_at=mt.ended_at, section=mt.section, section_started_at=mt.section_started_at,
        server_now=at, elapsed_seconds=elapsed, section_limit_seconds=LIMIT[mt.section],
        over_time=elapsed > LIMIT[mt.section], agenda=AGENDA,
        attendees=[AttendeeOut(id=u.id, name=u.name, role=u.role) for u in attendees],
        ratings=[RatingOut(user_id=r.user_id, rating=r.rating) for r in ratings],
        average_rating=None if average is None else float(average),
        new_todos=todos_out(db, list(new_todos), at),
    )


def _active(db: Session, team_id: str) -> m.Meeting | None:
    return db.scalars(select(m.Meeting).where(m.Meeting.team_id == team_id, m.Meeting.ended_at.is_(None))).first()


def _join(db: Session, meeting_id: str, user_id: str, at: datetime) -> None:
    db.execute(insert(m.MeetingAttendee).values(meeting_id=meeting_id, user_id=user_id, joined_at=at)
               .on_conflict_do_nothing(index_elements=["meeting_id", "user_id"]))


def _load_live(db: Session, meeting_id: str, user: CurrentUser, *, write: bool) -> m.Meeting:
    """Authorize, then lock the meeting row for this transaction. End, Advance, Rate and Join all come through
    here, so they serialise: a Rate or Join can never commit after an End it raced with."""
    load_in_team(db, m.Meeting, meeting_id, user, write=write, what="meeting")
    mt = db.scalars(select(m.Meeting).where(m.Meeting.id == meeting_id).with_for_update()
                    .execution_options(populate_existing=True)).one()
    if mt.ended_at is not None:
        raise conflict("meeting_ended", "this meeting has ended")
    return mt


def _require_facilitator(mt: m.Meeting, user: CurrentUser) -> None:
    if mt.facilitator_id != user.id:
        raise forbidden("only the facilitator can do this")


# ---------- reads ----------

@router.get("/teams/{team_id}/meetings/active", response_model=ActiveMeetingOut,
            summary="The team's live meeting, or null. Call on load, refresh and reconnect.")
def active_meeting(team: m.Team = Depends(team_reader), db: Session = Depends(get_db), at: datetime = Depends(now)):
    mt = _active(db, team.id)
    return ActiveMeetingOut(meeting=meeting_state(db, mt, at) if mt else None, server_now=at)


@router.get("/meetings/{meeting_id}", response_model=MeetingState, summary="A meeting's current state")
def get_meeting(meeting_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db),
                at: datetime = Depends(now)):
    return meeting_state(db, load_in_team(db, m.Meeting, meeting_id, user, write=False, what="meeting"), at)


# ---------- commands ----------

@router.post("/teams/{team_id}/meetings", response_model=MeetingState, status_code=status.HTTP_201_CREATED,
             summary="Start the Level 10 Meeting (you facilitate). If one is already live you join it (200).")
def start_meeting(response: Response, team: m.Team = Depends(team_writer), user: CurrentUser = Depends(current_user),
                  db: Session = Depends(get_db), at: datetime = Depends(now)):
    new_id = m.new_id("mt")
    inserted = db.execute(
        insert(m.Meeting).values(id=new_id, team_id=team.id, facilitator_id=user.id, started_at=at,
                                 section="scorecard", section_started_at=at)
        .on_conflict_do_nothing(index_elements=["team_id"], index_where=ACTIVE).returning(m.Meeting.id)
    ).scalar()
    mt = db.get(m.Meeting, inserted) if inserted else _active(db, team.id)
    if mt is None:  # the live one ended between our conflict and our read
        raise conflict("retry", "the team's meeting just changed; try again")
    _join(db, mt.id, user.id, at)
    publish(db, team.id, "meeting", meeting_id=mt.id)
    db.commit()
    if not inserted:
        response.status_code = status.HTTP_200_OK
    return meeting_state(db, mt, at)


@router.post("/meetings/{meeting_id}/join", response_model=MeetingState,
             summary="Join a live meeting (anyone who can read the team, Observers included)")
def join_meeting(meeting_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db),
                 at: datetime = Depends(now)):
    mt = _load_live(db, meeting_id, user, write=False)
    _join(db, mt.id, user.id, at)
    publish(db, mt.team_id, "meeting", meeting_id=mt.id)
    db.commit()
    return meeting_state(db, mt, at)


@router.post("/meetings/{meeting_id}/advance", response_model=MeetingState,
             summary="Facilitator only: move to the next agenda section")
def advance_meeting(meeting_id: IdPath, body: AdvanceIn, user: CurrentUser = Depends(current_user),
                    db: Session = Depends(get_db), at: datetime = Depends(now)):
    mt = _load_live(db, meeting_id, user, write=True)
    _require_facilitator(mt, user)
    if body.from_section == ORDER[-1]:
        raise conflict("last_section", "Conclude is the last section; end the meeting instead")
    target = ORDER[ORDER.index(body.from_section) + 1]
    moved = db.execute(
        update(m.Meeting).where(m.Meeting.id == mt.id, m.Meeting.section == body.from_section, m.Meeting.ended_at.is_(None))
        .values(section=target, section_started_at=at)
    ).rowcount
    if not moved:
        db.rollback()
        raise conflict("stale_section", "the meeting already moved on; refresh")
    publish(db, mt.team_id, "meeting", meeting_id=mt.id)
    db.commit()
    db.refresh(mt)
    return meeting_state(db, mt, at)


@router.post("/meetings/{meeting_id}/end", response_model=MeetingState, summary="Facilitator only: end the meeting")
def end_meeting(meeting_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db),
                at: datetime = Depends(now)):
    mt = _load_live(db, meeting_id, user, write=True)
    _require_facilitator(mt, user)
    db.execute(update(m.Meeting).where(m.Meeting.id == mt.id, m.Meeting.ended_at.is_(None)).values(ended_at=at))
    publish(db, mt.team_id, "meeting", meeting_id=mt.id)
    db.commit()
    db.refresh(mt)
    return meeting_state(db, mt, at)


@router.put("/meetings/{meeting_id}/rating", response_model=MeetingState,
            summary="Rate the meeting 1–10 during Conclude (attending Admins and Team Members); change it until the end")
def rate_meeting(meeting_id: IdPath, body: RatingIn, user: CurrentUser = Depends(current_user),
                 db: Session = Depends(get_db), at: datetime = Depends(now)):
    mt = _load_live(db, meeting_id, user, write=True)
    if mt.section != "conclude":
        raise conflict("not_conclude", "ratings open in Conclude")
    if db.get(m.MeetingAttendee, {"meeting_id": mt.id, "user_id": user.id}) is None:
        raise conflict("not_attending", "join the meeting to rate it")
    db.execute(insert(m.MeetingRating).values(meeting_id=mt.id, user_id=user.id, rating=body.rating, updated_at=at)
               .on_conflict_do_update(index_elements=["meeting_id", "user_id"], set_={"rating": body.rating, "updated_at": at}))
    publish(db, mt.team_id, "meeting", meeting_id=mt.id)
    db.commit()
    return meeting_state(db, mt, at)


# ---------- summary ----------

@router.get("/meetings/{meeting_id}/summary", response_model=SummaryOut,
            summary="An ended meeting's summary (seeded meetings included); 409 while it is live")
def meeting_summary(meeting_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db),
                    at: datetime = Depends(now)):
    mt = load_in_team(db, m.Meeting, meeting_id, user, write=False, what="meeting")
    if not engine.happened(mt.ended_at, at):  # not ended yet, as of now
        raise conflict("meeting_live", "the summary is available once the meeting ends")
    data = load_dataset(db, team_ids=[mt.team_id])
    meeting = next(x for x in data.meetings if x.id == mt.id)
    s = engine.meeting_summary(meeting, data.measurables, data.scores, data.issues, data.todos)
    names = {x.id: x.name for x in data.measurables}
    issue_titles = {i.id: i.title for i in db.scalars(select(m.Issue).where(m.Issue.id.in_(s["issues_solved"])))}
    todo_titles = {t.id: t.title for t in db.scalars(select(m.Todo).where(m.Todo.id.in_(s["todos_created"])))}
    people = {u.id: u for u in db.scalars(select(m.User).where(m.User.id.in_(s["attendee_ids"])))}
    return SummaryOut(
        meeting_id=mt.id, team_id=mt.team_id, started_at=mt.started_at, ended_at=mt.ended_at,
        scorecard_week=s["scorecard_week"],
        measurable_status=[SummaryMeasurable(id=k, name=names[k], status=v) for k, v in sorted(s["measurable_status"].items())],
        issues_solved=[ItemRef(id=i, title=issue_titles[i]) for i in s["issues_solved"]],
        todos_created=[ItemRef(id=t, title=todo_titles[t]) for t in s["todos_created"]],
        attendees=[AttendeeOut(id=u, name=people[u].name, role=people[u].role) for u in s["attendee_ids"]],
        rating=None if s["rating"] is None else float(s["rating"]),
    )
