"""Admins can own items in every team.

Brief 2.4: an Admin can do "everything a Team Member can, in every team", including owning
Issues and To-Dos. The (team_id, owner_id) → memberships foreign keys forbade that for Admins who
aren't members. Owners now reference users; "Admin, or a non-Observer member of this team" spans
tables, so the shared guard (`assert_valid_owner`) enforces it and API tests prove it.

Revision ID: 0002_admins_own_everywhere
Revises: 75e634dbe945
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_admins_own_everywhere"
down_revision: Union[str, None] = "75e634dbe945"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("measurables", "rocks", "issues", "todos")


def upgrade() -> None:
    for t in TABLES:
        op.drop_constraint(f"{t}_team_id_owner_id_fkey", t, type_="foreignkey")
        op.create_foreign_key(f"{t}_owner_id_fkey", t, "users", ["owner_id"], ["id"])


def downgrade() -> None:
    for t in TABLES:
        op.drop_constraint(f"{t}_owner_id_fkey", t, type_="foreignkey")
        op.create_foreign_key(f"{t}_team_id_owner_id_fkey", t, "memberships", ["team_id", "owner_id"], ["team_id", "user_id"])
