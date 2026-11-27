"""Bonus: Make it an Issue from an off-track Rock or an overdue To-Do, with the same no-duplicates rule.

- issues.source_rock_id / source_todo_id, same-team composite FKs (to UNIQUE (id, team_id) on rocks / todos)
- an Issue has at most one source (a Scorecard cell, a Rock or a To-Do)
- one unsolved Issue per Rock and per To-Do: partial unique indexes the API's ON CONFLICT relies on
- full indexes for the foreign keys the app reads through (a partial index can't serve every lookup):
  issues by source Rock / To-Do (open_issue_id), issues by team (all states), meetings by team

Revision ID: 0007_issue_from_rock_or_todo
Revises: 0006_indexes_for_read_paths
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_issue_from_rock_or_todo"
down_revision: Union[str, None] = "0006_indexes_for_read_paths"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ONE_SOURCE = ("(source_measurable_id IS NOT NULL)::int + (source_rock_id IS NOT NULL)::int"
              " + (source_todo_id IS NOT NULL)::int <= 1")


def upgrade() -> None:
    op.add_column("issues", sa.Column("source_rock_id", sa.String(64), nullable=True))
    op.add_column("issues", sa.Column("source_todo_id", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_rocks_id_team", "rocks", ["id", "team_id"])
    op.create_unique_constraint("uq_todos_id_team", "todos", ["id", "team_id"])
    op.create_foreign_key("issues_source_rock_same_team_fkey", "issues", "rocks",
                          ["source_rock_id", "team_id"], ["id", "team_id"])
    op.create_foreign_key("issues_source_todo_same_team_fkey", "issues", "todos",
                          ["source_todo_id", "team_id"], ["id", "team_id"])
    op.create_check_constraint("issue_at_most_one_source", "issues", ONE_SOURCE)
    op.create_index("uq_issue_open_per_source_rock", "issues", ["source_rock_id", "team_id"], unique=True,
                    postgresql_where=sa.text("solved_at IS NULL AND source_rock_id IS NOT NULL"))
    op.create_index("uq_issue_open_per_source_todo", "issues", ["source_todo_id", "team_id"], unique=True,
                    postgresql_where=sa.text("solved_at IS NULL AND source_todo_id IS NOT NULL"))
    op.create_index("ix_issues_source_rock", "issues", ["source_rock_id", "team_id"])
    op.create_index("ix_issues_source_todo", "issues", ["source_todo_id", "team_id"])
    op.drop_index("ix_issue_team_open", table_name="issues")  # superseded by a full index on team
    op.create_index("ix_issues_team", "issues", ["team_id"])
    op.create_index("ix_meetings_team", "meetings", ["team_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_meetings_team", table_name="meetings")
    op.drop_index("ix_issues_team", table_name="issues")
    op.create_index("ix_issue_team_open", "issues", ["team_id"], postgresql_where=sa.text("solved_at IS NULL"))
    op.drop_index("ix_issues_source_todo", table_name="issues")
    op.drop_index("ix_issues_source_rock", table_name="issues")
    op.drop_index("uq_issue_open_per_source_todo", table_name="issues")
    op.drop_index("uq_issue_open_per_source_rock", table_name="issues")
    op.drop_constraint("issue_at_most_one_source", "issues", type_="check")
    op.drop_constraint("issues_source_todo_same_team_fkey", "issues", type_="foreignkey")
    op.drop_constraint("issues_source_rock_same_team_fkey", "issues", type_="foreignkey")
    op.drop_constraint("uq_todos_id_team", "todos", type_="unique")
    op.drop_constraint("uq_rocks_id_team", "rocks", type_="unique")
    op.drop_column("issues", "source_todo_id")
    op.drop_column("issues", "source_rock_id")
