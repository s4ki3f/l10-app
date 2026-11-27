"""Bonus: the audit trail. Read-only: events are written by the write paths themselves (app.audit.record)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ..deps import get_db, team_reader
from ..schemas import AuditEntity, AuditEntityType, AuditEventOut, AuditPage, PersonRef

router = APIRouter(tags=["audit"])


@router.get("/teams/{team_id}/audit", response_model=AuditPage,
            summary="Bonus — the team's audit trail, newest first; cursor-paginated; optionally one record's history")
def team_audit(
    team: m.Team = Depends(team_reader),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, ge=1, description="Only events older than this id (from next_before)"),
    entity_type: AuditEntityType | None = Query(None, description="Only events about this kind of record"),
    entity_id: str | None = Query(None, pattern=r"^[A-Za-z0-9_:-]{1,96}$", description=(
        "One record's history: an Issue, To-Do or meeting id, or a score cell as <measurable id>:<YYYY-MM-DD>")),
):
    q = select(m.AuditEvent).where(m.AuditEvent.team_id == team.id)  # never another team's events
    if before is not None:
        q = q.where(m.AuditEvent.id < before)
    if entity_type is not None:
        q = q.where(m.AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        q = q.where(m.AuditEvent.entity_id == entity_id)
    rows = db.scalars(q.order_by(m.AuditEvent.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    actors = {u.id: u.name for u in db.scalars(select(m.User).where(m.User.id.in_({r.actor_id for r in page})))} if page else {}
    return AuditPage(
        items=[AuditEventOut(id=r.id, at=r.at, actor=PersonRef(id=r.actor_id, name=actors[r.actor_id]), action=r.action,
                             entity=AuditEntity(type=r.entity_type, id=r.entity_id, label=r.entity_label),
                             changes=r.changes) for r in page],
        next_before=page[-1].id if len(rows) > limit else None,
    )
