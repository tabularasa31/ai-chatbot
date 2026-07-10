"""Add chats.last_detected_language for the session-level detection fallback.

Stores the last reliable per-turn detected_language of a chat. Short follow-up
turns ("Yes", "ok?") and locked chats (detection skipped) produce
detected_language="unknown"; observability metadata then backfills from this
column with resolution reason "session_fallback". The column never feeds
response_language resolution. Idempotent on upgrade; the downgrade is for
documentation only and must never be run against shared/production DBs.

Revision ID: chat_last_detected_language_v1
Revises: chat_drop_awaited_reply_v1
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "chat_last_detected_language_v1"
down_revision = "chat_drop_awaited_reply_v1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(col["name"] == column for col in inspect(bind).get_columns(table))


def upgrade() -> None:
    if not _has_column("chats", "last_detected_language"):
        op.add_column(
            "chats",
            sa.Column("last_detected_language", sa.String(length=16), nullable=True),
        )


def downgrade() -> None:
    # Documentation only — never run against shared/production DBs.
    if _has_column("chats", "last_detected_language"):
        with op.batch_alter_table("chats") as batch_op:
            batch_op.drop_column("last_detected_language")
