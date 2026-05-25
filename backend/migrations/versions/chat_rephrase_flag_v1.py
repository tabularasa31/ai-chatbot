"""Add last_reply_was_rephrase_prompt column to chats table.

Tracks whether the previous bot reply in this session was the "soft rephrase"
prompt emitted when RAG returns strictly zero hits. On the next user turn,
the chat pipeline reads this flag to decide whether a second consecutive
zero-hits turn should fall through to the LLM relevance model (and possibly
escalate) instead of repeating the soft-reply.

Revision ID: chat_rephrase_flag_v1
Revises: chat_session_ended_event_at_v1
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "chat_rephrase_flag_v1"
down_revision = "chat_session_ended_event_at_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column(
            "last_reply_was_rephrase_prompt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chats", "last_reply_was_rephrase_prompt")
