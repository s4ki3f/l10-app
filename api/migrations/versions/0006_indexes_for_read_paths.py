"""Index the foreign keys the app reads through.

Postgres doesn't index foreign keys on its own. These five are on request paths: every sign-in check
(memberships by user), every Team page (measurables/rocks by team, milestones by rock) and the Scorecard's
open-Issue lookup (issues by source measurable). The remaining foreign keys are only used when deleting a
user, company or Issue, which the app never does; tests/db/test_schema.py lists them on purpose.

Revision ID: 0006_indexes_for_read_paths
Revises: 0005_versions_for_lost_updates
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_indexes_for_read_paths"
down_revision: Union[str, None] = "0005_versions_for_lost_updates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("ix_memberships_user", "memberships", ["user_id"]),
    ("ix_measurables_team", "measurables", ["team_id"]),
    ("ix_rocks_team", "rocks", ["team_id"]),
    ("ix_milestones_rock", "milestones", ["rock_id"]),
    ("ix_issues_source_measurable", "issues", ["source_measurable_id", "team_id"]),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in INDEXES:
        op.drop_index(name, table_name=table)
