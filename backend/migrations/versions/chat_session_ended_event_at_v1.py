"""Add chats.session_ended_event_at — analytics marker for the session sweeper.

The inactivity sweeper (``backend.jobs.chat_session_sweeper``) needs a durable,
idempotent way to record that it has already emitted ``chat_session_ended`` for
a chat. It cannot reuse ``chats.ended_at`` for this because ``ended_at`` also
closes the conversation (the escalation FSM routes any later turn on a closed
chat to the "chat already closed" handler). Reporting a session as ended for
analytics must not make the chat un-resumable, so this is a separate nullable
timestamp.

Revision ID: chat_session_ended_event_at_v1
Revises: escalation_followup_email_v1
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "chat_session_ended_event_at_v1"
down_revision = "escalation_followup_email_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("chats")}
    if "session_ended_event_at" not in cols:
        op.add_column(
            "chats",
            sa.Column("session_ended_event_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    # Documented for completeness only — never run against shared DBs.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("chats")}
    if "session_ended_event_at" in cols:
        op.drop_column("chats", "session_ended_event_at")
