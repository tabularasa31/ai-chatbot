"""Drop the now-unused last_reply_awaited_reply column from chats.

The column was read only by the small-talk fast path (SmallTalkHandler), which
has been removed — every non-empty user turn now flows through RAG. With no
remaining readers the flag is dead, so drop it. Idempotent on upgrade; the
downgrade re-adds the column (without backfill) for documentation only and must
never be run against shared/production DBs.

Revision ID: chat_drop_awaited_reply_v1
Revises: escalation_awaiting_request_v1
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "chat_drop_awaited_reply_v1"
down_revision = "escalation_awaiting_request_v1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(col["name"] == column for col in inspect(bind).get_columns(table))


def upgrade() -> None:
    # Idempotent: skip if a prior partial run already dropped the column.
    # batch_alter_table keeps the drop portable to SQLite (copy-and-move), which
    # older versions need since they can't ALTER TABLE DROP COLUMN in place; on
    # Postgres (prod) it emits a plain ALTER.
    if _has_column("chats", "last_reply_awaited_reply"):
        with op.batch_alter_table("chats") as batch_op:
            batch_op.drop_column("last_reply_awaited_reply")


def downgrade() -> None:
    # Documentation only — never run against shared/production DBs.
    if not _has_column("chats", "last_reply_awaited_reply"):
        with op.batch_alter_table("chats") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "last_reply_awaited_reply",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )
