"""Add last_reply_awaited_reply column to chats table.

Tracks whether the previous bot reply in this session ended awaiting a user
answer (a clarify/slot question). On the next user turn SmallTalkHandler reads
this flag to suppress the greeting fast path, so a one-word reply that answers
the bot's question reaches RAG instead of being greeted.

Revision ID: chat_awaited_reply_v1
Revises: chat_rephrase_flag_v1
Create Date: 2026-05-28
"""
# ruff: noqa: RUF001 — fullwidth/Arabic question marks are intentional in the
# backfill LIKE patterns below.
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "chat_awaited_reply_v1"
down_revision = "chat_rephrase_flag_v1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(col["name"] == column for col in inspect(bind).get_columns(table))


# Backfill for chats that predate the column: arm the flag where the latest
# assistant message in an open chat already ended with a question mark, so the
# first one-word reply after deploy (e.g. answering "example.com") still reaches
# RAG instead of being swallowed by the greeting fast path. rtrim() only trims
# spaces, which is sufficient for a best-effort one-time backfill.
_BACKFILL_SQL = """
UPDATE chats
SET last_reply_awaited_reply = true
WHERE ended_at IS NULL
  AND id IN (
      SELECT m.chat_id
      FROM messages m
      WHERE m.role = 'assistant'
        AND m.created_at = (
            SELECT MAX(m2.created_at)
            FROM messages m2
            WHERE m2.chat_id = m.chat_id AND m2.role = 'assistant'
        )
        AND (
            rtrim(m.content) LIKE '%?'
            OR rtrim(m.content) LIKE '%？'
            OR rtrim(m.content) LIKE '%؟'
        )
  )
"""


def upgrade() -> None:
    # Idempotent: skip if a prior partial run already added the column.
    if not _has_column("chats", "last_reply_awaited_reply"):
        op.add_column(
            "chats",
            sa.Column(
                "last_reply_awaited_reply",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("chats", "last_reply_awaited_reply")
