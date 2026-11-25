"""Issues and the To-Dos that come from them. Order and flags are computed by app.rules, never in the browser."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ...db.load import issue_of, todo_of
from ...realtime import publish
from ...rules import engine
from ...rules.weeks import local_date
from ..deps import (
    CurrentUser, assert_valid_owner, current_user, get_db, load_in_team, now, team_reader, team_writer,
)
from ..schemas import IssueCreate, IssueOut, IssueSource, PersonRef, TodoCreate, TodoOut

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
            id=r.id, team_id=r.team_id, title=r.title, owner=PersonRef(id=r.owner_id, name=names[r.owner_id]),
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
            source_issue=PersonRef(id=r.source_issue_id, name=titles[r.source_issue_id]) if r.source_issue_id else None,
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
    issue = m.Issue(id=m.new_id("i"), team_id=team.id, title=body.title, owner_id=user.id, priority=body.priority, created_at=at)
    db.add(issue)
    db.flush()
    publish(db, team.id, "issues")
    db.commit()
    return issues_out(db, [issue])[0]


@router.post("/issues/{issue_id}/todos", response_model=TodoOut, status_code=status.HTTP_201_CREATED,
             summary="Add a To-Do to an Issue: due in 7 days, owner from the team's Admins and Team Members")
def add_todo(
    issue_id: str,
    body: TodoCreate,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    issue = load_in_team(db, m.Issue, issue_id, user, write=True, what="issue")
    assert_valid_owner(db, issue.team_id, body.owner_id)
    todo = m.Todo(
        id=m.new_id("t"), team_id=issue.team_id, title=body.title, owner_id=body.owner_id,
        due_date=local_date(at) + timedelta(days=TODO_DUE_DAYS), created_at=at, source_issue_id=issue.id,
    )
    db.add(todo)
    db.flush()
    publish(db, issue.team_id, "todos")
    db.commit()
    return todos_out(db, [todo], at)[0]


@router.get("/teams/{team_id}/todos", response_model=list[TodoOut], summary="The team's To-Dos, by due date")
def list_todos(team: m.Team = Depends(team_reader), db: Session = Depends(get_db), at: datetime = Depends(now)):
    rows = db.scalars(
        select(m.Todo).where(m.Todo.team_id == team.id, m.Todo.created_at <= at).order_by(m.Todo.due_date, m.Todo.id)
    ).all()
    return todos_out(db, list(rows), at)
