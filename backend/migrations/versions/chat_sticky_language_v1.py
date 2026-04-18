"""Add Chat.last_response_language for weighted sticky language detection.

Revision ID: chat_sticky_language_v1
Revises: gap_analyzer_indexes_v1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "chat_sticky_language_v1"
down_revision = "gap_analyzer_indexes_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing_cols = {column["name"] for column in insp.get_columns("chats")}
    if "last_response_language" not in existing_cols:
        op.add_column(
            "chats",
            sa.Column("last_response_language", sa.String(length=16), nullable=True),
        )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
