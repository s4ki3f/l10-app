"""Least privilege: the API connects as its own role, so even the app's credentials can't rewrite history.

- l10_app may SELECT, INSERT and UPDATE the data; on audit_events only SELECT and INSERT. It has no DELETE or
  TRUNCATE anywhere (the app never deletes) and owns nothing, so it can't disable the append-only trigger or
  change the schema either. The trigger's purge opt-in is useless to it: the DELETE is refused before the
  trigger runs. Migrations and `seed --reset` keep running as the owner.
- The role is created here if it's missing, with L10_APP_DB_PASSWORD (default "l10_app", for local use). In
  production, create it beforehand with its own secret: this migration then only grants.
- Tables added by later migrations get SELECT, INSERT, UPDATE by default; an append-only one must revoke UPDATE.

Revision ID: 0009_app_role
Revises: 0008_audit_trail
"""
import os
import re
from typing import Sequence, Union

from alembic import op

revision: str = "0009_app_role"
down_revision: Union[str, None] = "0008_audit_trail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = "l10_app"


def _password() -> str:
    password = os.environ.get("L10_APP_DB_PASSWORD", "l10_app")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", password):  # it goes into SQL as a literal: keep it plain
        raise ValueError("L10_APP_DB_PASSWORD may only use letters, digits, '_' and '-'")
    return password


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
            CREATE ROLE {ROLE} LOGIN PASSWORD '{_password()}';
          END IF;
        END $$""")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {ROLE}")
    op.execute(f"REVOKE UPDATE ON audit_events FROM {ROLE}")  # history: read and append, nothing else
    op.execute(f"REVOKE INSERT, UPDATE ON alembic_version FROM {ROLE}")  # the schema version belongs to migrations
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO {ROLE}")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE ON TABLES FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    # the role stays: roles are cluster-wide, and another database (the test one) may still use it
