"""Same-team links, time order, and app write times.

- A To-Do's source Issue and an Issue's source Measurable must be in the same team (composite FKs
  to UNIQUE (id, team_id)), so the database rejects cross-team links, not only the API.
- Nothing is solved or done before it was created.
- issues.updated_at / todos.updated_at record app writes; the API's clock resumes after the latest
  app write on restart, so a restart never makes recent rows look like the future.

Revision ID: 0003_integrity_and_clock
Revises: 0002_admins_own_everywhere
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_integrity_and_clock"
down_revision: Union[str, None] = "0002_admins_own_everywhere"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_issues_id_team", "issues", ["id", "team_id"])
    op.create_unique_constraint("uq_measurables_id_team", "measurables", ["id", "team_id"])
    op.drop_constraint("todos_source_issue_id_fkey", "todos", type_="foreignkey")
    op.create_foreign_key("todos_source_issue_same_team_fkey", "todos", "issues",
                          ["source_issue_id", "team_id"], ["id", "team_id"])
    op.drop_constraint("issues_source_measurable_id_fkey", "issues", type_="foreignkey")
    op.create_foreign_key("issues_source_measurable_same_team_fkey", "issues", "measurables",
                          ["source_measurable_id", "team_id"], ["id", "team_id"])
    op.create_check_constraint("issue_solved_after_created", "issues", "solved_at IS NULL OR solved_at >= created_at")
    op.create_check_constraint("todo_done_after_created", "todos", "done_at IS NULL OR done_at >= created_at")
    op.add_column("issues", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("todos", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("todos", "updated_at")
    op.drop_column("issues", "updated_at")
    op.drop_constraint("todo_done_after_created", "todos", type_="check")
    op.drop_constraint("issue_solved_after_created", "issues", type_="check")
    op.drop_constraint("issues_source_measurable_same_team_fkey", "issues", type_="foreignkey")
    op.create_foreign_key("issues_source_measurable_id_fkey", "issues", "measurables", ["source_measurable_id"], ["id"])
    op.drop_constraint("todos_source_issue_same_team_fkey", "todos", type_="foreignkey")
    op.create_foreign_key("todos_source_issue_id_fkey", "todos", "issues", ["source_issue_id"], ["id"])
    op.drop_constraint("uq_measurables_id_team", "measurables", type_="unique")
    op.drop_constraint("uq_issues_id_team", "issues", type_="unique")
