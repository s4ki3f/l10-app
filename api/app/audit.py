"""The audit trail: who changed what, when — written in the same transaction as the change.

Call `record()` next to `publish()` in every write path, before the commit: if the change rolls back, so does
its event; a refused or no-op request (409, 403, an Issue that already existed) records nothing. The table is
append-only (a database trigger), so history can't be edited after the fact.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db.models import AuditEvent


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def score_entity_id(measurable_id: str, week: date) -> str:
    return f"{measurable_id}:{week.isoformat()}"


def record(db: Session, *, team_id: str, actor_id: str, action: str, entity_type: str, entity_id: str,
           label: str, at: datetime, changes: dict | None = None) -> None:
    db.execute(insert(AuditEvent).values(
        team_id=team_id, actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
        entity_label=label, changes=_jsonable(changes or {}), at=at,
    ))
