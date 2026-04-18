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
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
