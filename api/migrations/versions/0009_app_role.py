"""Least privilege: the API connects as its own role, so even the app's credentials can't rewrite history.

l10_app reads everything but writes only what the app writes:
- INSERT and UPDATE on issues, todos, scores, meetings and meeting_ratings; INSERT on meeting_attendees.
- audit_events: INSERT into every column except `id`, so it can append but never choose an event's place in
  history (OVERRIDING SYSTEM VALUE and COPY need the id column), and never update, delete or truncate.
- users, teams, memberships, measurables, Rocks and the rest are read-only to it (renaming a person would
  rewrite every past event's actor). It deletes nothing and owns nothing, so it can't disable the append-only
  trigger or change the schema. A table added later is read-only to it until a migration grants more: a
  missing grant then fails the API tests (they run as this role) instead of quietly widening its rights.

The role is created here (NOLOGIN) if it's missing; `python -m app.db.app_role`, which compose runs after the
migrations on every `up`, gives it LOGIN and its password. An operator may create it beforehand instead; it
must then be a plain role, which is checked.

Revision ID: 0009_app_role
Revises: 0008_audit_trail
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009_app_role"
down_revision: Union[str, None] = "0008_audit_trail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = "l10_app"
WRITES = {  # table → what the app does to it, beyond reading
    "issues": "INSERT, UPDATE", "todos": "INSERT, UPDATE", "scores": "INSERT, UPDATE",
    "meetings": "INSERT, UPDATE", "meeting_ratings": "INSERT, UPDATE", "meeting_attendees": "INSERT",
}
AUDIT_COLUMNS = "team_id, actor_id, action, entity_type, entity_id, entity_label, changes, at"  # not id

PLAIN_ROLE = f"""
DO $$
BEGIN
  BEGIN
    CREATE ROLE {ROLE} NOLOGIN;
  EXCEPTION WHEN duplicate_object THEN NULL;  -- created by an operator, or by a concurrent first run
  END;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}'
             AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION 'role {ROLE} must be a plain role: no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS';
  END IF;
  IF EXISTS (SELECT FROM pg_auth_members WHERE member = '{ROLE}'::regrole) THEN
    RAISE EXCEPTION 'role {ROLE} must not be a member of another role: it would inherit that role''s rights';
  END IF;
END $$"""


def upgrade() -> None:
    op.execute(PLAIN_ROLE)
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")  # the Postgres 15+ default, made explicit
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}")
    for table, privileges in WRITES.items():
        op.execute(f"GRANT {privileges} ON {table} TO {ROLE}")
    op.execute(f"GRANT INSERT ({AUDIT_COLUMNS}) ON audit_events TO {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE}")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {ROLE}")
    op.execute(f"REVOKE INSERT ({AUDIT_COLUMNS}) ON audit_events FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    # The role stays: roles are cluster-wide, and another database (the test one) may still use it. CREATE on
    # public stays revoked from PUBLIC: a downgrade shouldn't reopen a hole.
