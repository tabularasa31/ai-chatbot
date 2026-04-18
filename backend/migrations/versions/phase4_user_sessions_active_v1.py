"""Add unique partial index for active user_sessions rows.

Revision ID: phase4_user_sessions_active_v1
Revises: phase4_message_embeddings_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "phase4_user_sessions_active_v1"
down_revision = "phase4_message_embeddings_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_user_sessions_client_user_active",
        "user_sessions",
        ["client_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("session_ended_at IS NULL"),
    )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
