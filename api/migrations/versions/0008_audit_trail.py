"""Bonus: an audit trail — who changed what, when, before and after.

- audit_events: one row per change, written in the same transaction as the change (app.audit.record)
- append-only, enforced by the database: a trigger rejects UPDATE, DELETE and TRUNCATE. The one exception is
  the operator's `seed --reset`, which restores exactly the seed and must opt in with
  SET LOCAL l10.audit_purge = 'on' (the application never sets it; a test checks that)
- ids come from an identity column the application can't set; time is the app clock (APP_NOW), like every row

Revision ID: 0008_audit_trail
Revises: 0007_issue_from_rock_or_todo
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_audit_trail"
down_revision: Union[str, None] = "0007_issue_from_rock_or_todo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APPEND_ONLY = """
CREATE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('l10.audit_purge', true) = 'on' THEN
    RETURN OLD;  -- seed --reset only, explicitly opted in for this transaction
  END IF;
  RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP USING ERRCODE = 'restrict_violation';
END $$;
CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("actor_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.String(96), nullable=False),
        sa.Column("entity_label", sa.Text(), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(r"action ~ '^[a-z]+\.[a-z_]+$'", name="audit_action_format"),
        sa.CheckConstraint("entity_type IN ('issue', 'todo', 'score', 'meeting')", name="audit_entity_type_valid"),
        sa.CheckConstraint("jsonb_typeof(changes) = 'object'", name="audit_changes_object"),
    )
    op.create_index("ix_audit_team", "audit_events", ["team_id", "id"])
    op.create_index("ix_audit_entity", "audit_events", ["entity_type", "entity_id", "id"])
    op.execute(APPEND_ONLY)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
    op.drop_index("ix_audit_entity", table_name="audit_events")
    op.drop_index("ix_audit_team", table_name="audit_events")
    op.drop_table("audit_events")
