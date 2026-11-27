"""Ratings record when they were written, so the clock can resume after them too.

Found by an external review: a Rate was the one app write the clock-resume rule couldn't see.

Revision ID: 0004_rating_write_time
Revises: 0003_integrity_and_clock
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_rating_write_time"
down_revision: Union[str, None] = "0003_integrity_and_clock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meeting_ratings", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("meeting_ratings", "updated_at")
