"""Issues and the To-Dos that come from them. Order and flags are computed by app.rules, never in the browser."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import issue_of, todo_of
from ...realtime import publish
from ...rules import engine
from ...rules.weeks import last_closed_week, local_date
from ..deps import (
    CurrentUser, assert_valid_owner, current_user, get_db, load_in_team, now, team_reader, team_writer,
)
from ..errors import AppError, conflict
from ..schemas import (
    CompletionOut, IdPath, IssueCreate, ItemRef, IssueOut, IssuePatch, IssueSource, PersonRef, SolveIn, TodoCreate, TodoList, TodoOut,
)

router = APIRouter(tags=["issues"])

TODO_DUE_DAYS = 7


def _people(db: Session, ids) -> dict[str, str]:
    ids = set(ids)
    return {u.id: u.name for u in db.scalars(select(m.User).where(m.User.id.in_(ids)))} if ids else {}


def issues_out(db: Session, rows: list[m.Issue]) -> list[IssueOut]:
    names = _people(db, (r.owner_id for r in rows))
    src_ids = {r.source_measurable_id for r in rows if r.source_measurable_id}
    measurables = {x.id: x.name for x in db.scalars(select(m.Measurable).where(m.Measurable.id.in_(src_ids)))} if src_ids else {}
    return [
        IssueOut(
            id=r.id, version=r.version, team_id=r.team_id, title=r.title, owner=PersonRef(id=r.owner_id, name=names[r.owner_id]),
            priority=r.priority, created_at=r.created_at, solved_at=r.solved_at, resolution_note=r.resolution_note,
            source=IssueSource(measurable_id=r.source_measurable_id, measurable_name=measurables[r.source_measurable_id],
                               week_start=r.source_week) if r.source_measurable_id else None,
        )
        for r in rows
    ]


def todos_out(db: Session, rows: list[m.Todo], at: datetime) -> list[TodoOut]:
    names = _people(db, (r.owner_id for r in rows))
    src = {r.source_issue_id for r in rows if r.source_issue_id}
    titles = {i.id: i.title for i in db.scalars(select(m.Issue).where(m.Issue.id.in_(src)))} if src else {}
    return [
        TodoOut(
            id=r.id, team_id=r.team_id, title=r.title, owner=PersonRef(id=r.owner_id, name=names[r.owner_id]),
            due_date=r.due_date, done_at=r.done_at if engine.happened(r.done_at, at) else None, created_at=r.created_at,
            overdue=engine.is_overdue(todo_of(r), at),
            source_issue=ItemRef(id=r.source_issue_id, title=titles[r.source_issue_id]) if r.source_issue_id else None,
        )
        for r in rows
    ]


@router.get("/teams/{team_id}/issues", response_model=list[IssueOut],
            summary="Unsolved Issues in Issue order, or solved Issues newest first")
def list_issues(
    team: m.Team = Depends(team_reader),
    state: str = Query("open", pattern="^(open|solved)$"),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    rows = {r.id: r for r in db.scalars(select(m.Issue).where(m.Issue.team_id == team.id))}
    if state == "open":
        ordered = [rows[i.id] for i in engine.issue_order([issue_of(r) for r in rows.values()], at)]
    else:
        ordered = sorted((r for r in rows.values() if engine.happened(r.solved_at, at)), key=lambda r: r.solved_at, reverse=True)
    return issues_out(db, ordered)


@router.post("/teams/{team_id}/issues", response_model=IssueOut, status_code=status.HTTP_201_CREATED,
             summary="Create an Issue owned by you")
def create_issue(
    body: IssueCreate,
    team: m.Team = Depends(team_writer),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    assert_valid_owner(db, team.id, user.id)
    issue = m.Issue(id=m.new_id("i"), team_id=team.id, title=body.title, owner_id=user.id, priority=body.priority,
                    created_at=at, updated_at=at)
    db.add(issue)
    db.flush()
    publish(db, team.id, "issues")
    db.commit()
    return issues_out(db, [issue])[0]


@router.post("/issues/{issue_id}/todos", response_model=TodoOut, status_code=status.HTTP_201_CREATED,
             summary="Add a To-Do to an Issue: due in 7 days, owner from the team's Admins and Team Members")
def add_todo(
    issue_id: IdPath,
    body: TodoCreate,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    issue = load_in_team(db, m.Issue, issue_id, user, write=True, what="issue")
    assert_valid_owner(db, issue.team_id, body.owner_id)
    todo = m.Todo(
        id=m.new_id("t"), team_id=issue.team_id, title=body.title, owner_id=body.owner_id,
        due_date=local_date(at) + timedelta(days=TODO_DUE_DAYS), created_at=at, updated_at=at, source_issue_id=issue.id,
    )
    db.add(todo)
    db.flush()
    publish(db, issue.team_id, "todos")
    db.commit()
    return todos_out(db, [todo], at)[0]


@router.get("/teams/{team_id}/todos", response_model=TodoList,
            summary="The team's To-Dos by due date (overdue flagged) and the last closed week's completion rate")
def list_todos(team: m.Team = Depends(team_reader), db: Session = Depends(get_db), at: datetime = Depends(now)):
    rows = db.scalars(
        select(m.Todo).where(m.Todo.team_id == team.id, m.Todo.created_at <= at).order_by(m.Todo.due_date, m.Todo.id)
    ).all()
    week = last_closed_week(at)
    rate = engine.todo_completion([todo_of(r) for r in rows], week, at)
    return TodoList(items=todos_out(db, list(rows), at), last_closed_week=week,
                    completion=CompletionOut(week_start=week, **rate) if rate else None)


# ---------- changing Issues ----------

@router.patch("/issues/{issue_id}", response_model=IssueOut, summary="Set or clear an unsolved Issue's priority")
def set_priority(issue_id: IdPath, body: IssuePatch, user: CurrentUser = Depends(current_user),
                 db: Session = Depends(get_db), at: datetime = Depends(now)):
    issue = load_in_team(db, m.Issue, issue_id, user, write=True, what="issue")
    # Check-and-write in ONE statement: "still unsolved as of now" (and, if given, "still the version you saw")
    # are WHERE conditions, so a solve that commits between our read and our write can't be overwritten.
    stmt = update(m.Issue).where(m.Issue.id == issue.id, _unsolved_as_of(at))
    if body.expected_version is not None:
        stmt = stmt.where(m.Issue.version == body.expected_version)
    changed = db.execute(stmt.values(priority=body.priority, version=m.Issue.version + 1, updated_at=at)
                         .returning(m.Issue.id)).scalar()
    if changed is None:
        db.rollback()
        current = db.get(m.Issue, issue.id, populate_existing=True)
        if engine.happened(current.solved_at, at):
            raise conflict("issue_solved", "a solved Issue can't be reprioritized")
        raise AppError(409, "stale_write", "someone changed this Issue's priority after you loaded it",
                       current={"priority": current.priority, "version": current.version})
    publish(db, issue.team_id, "issues")
    db.commit()
    db.refresh(issue)
    return issues_out(db, [issue])[0]


@router.post("/issues/{issue_id}/solve", response_model=IssueOut, summary="Solve an Issue; a resolution note is required")
def solve_issue(issue_id: IdPath, body: SolveIn, user: CurrentUser = Depends(current_user),
                db: Session = Depends(get_db), at: datetime = Depends(now)):
    issue = load_in_team(db, m.Issue, issue_id, user, write=True, what="issue")
    # Same pattern: two people solving at once — the second can't overwrite the first one's note.
    solved = db.execute(update(m.Issue).where(m.Issue.id == issue.id, _unsolved_as_of(at))
                        .values(solved_at=at, resolution_note=body.note, updated_at=at, version=m.Issue.version + 1)
                        .returning(m.Issue.id)).scalar()
    if solved is None:
        db.rollback()
        raise conflict("issue_solved", "this Issue is already solved")
    publish(db, issue.team_id, "issues")
    db.commit()
    db.refresh(issue)
    return issues_out(db, [issue])[0]


def _unsolved_as_of(at: datetime):
    """Not solved by `at` (solved later than `at` counts as unsolved, matching the Issues list)."""
    return or_(m.Issue.solved_at.is_(None), m.Issue.solved_at > at)


# ---------- To-Dos: done / undo ----------

def _set_done(db: Session, todo_id: str, user: CurrentUser, at: datetime, done: bool) -> TodoOut:
    todo = load_in_team(db, m.Todo, todo_id, user, write=True, what="to-do")
    todo.done_at, todo.updated_at = (at if done else None), at
    publish(db, todo.team_id, "todos")
    db.commit()
    return todos_out(db, [todo], at)[0]


@router.post("/todos/{todo_id}/done", response_model=TodoOut, summary="Mark a To-Do done (done time = now)")
def mark_done(todo_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db),
              at: datetime = Depends(now)):
    return _set_done(db, todo_id, user, at, True)


@router.post("/todos/{todo_id}/undo", response_model=TodoOut, summary="Undo: mark a To-Do not done")
def undo_done(todo_id: IdPath, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db),
              at: datetime = Depends(now)):
    return _set_done(db, todo_id, user, at, False)
