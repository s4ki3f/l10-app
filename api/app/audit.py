"""The audit trail: who changed what, when — written in the same transaction as the change.

Call `record()` next to `publish()` in every write path, before the commit: if the change rolls back, so does
its event. A refused request (409, 403) records nothing, and so does one that changes nothing (the same value
again, an Issue that already existed, a re-join): those return without writing at all.

`changes` maps each field to [before, after] (a CHECK enforces the shape). "Before" is as of now: a fact dated
after the app's now hasn't happened yet, so it reads as empty. On a creation, before is null and empty fields are
left out; a related record is stored as {id, name} or {id, title}, as it was at the time. The table is
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


def newly_set(**fields) -> dict:
    """Changes for fields that go from nothing to a value (a creation, a resolution note); empty ones are left out."""
    return {k: [None, v] for k, v in fields.items() if v is not None}


def score_entity_id(measurable_id: str, week: date) -> str:
    return f"{measurable_id}:{week.isoformat()}"


def record(db: Session, *, team_id: str, actor_id: str, action: str, entity_type: str, entity_id: str,
           label: str, at: datetime, changes: dict | None = None) -> None:
    db.execute(insert(AuditEvent).values(
        team_id=team_id, actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
        entity_label=label, changes=_jsonable(changes or {}), at=at,
    ))
