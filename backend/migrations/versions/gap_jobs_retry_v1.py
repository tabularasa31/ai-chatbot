"""Raise default max_attempts for Gap Analyzer jobs.

Revision ID: gap_jobs_retry_v1
Revises: chat_sticky_language_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "gap_jobs_retry_v1"
down_revision = "chat_sticky_language_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gap_analyzer_jobs") as batch_op:
        batch_op.alter_column(
            "max_attempts",
            server_default="5",
            existing_type=sa.Integer(),
            existing_nullable=False,
        )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
