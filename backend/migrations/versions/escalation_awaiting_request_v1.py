"""Add escalation_awaiting_request to chats.

Tracks the state where the user asked for a human but has not yet stated a
forwardable problem; the bot elicits the actual question instead of minting an
empty ticket.

Revision ID: escalation_awaiting_request_v1
Revises: chat_awaited_reply_v1
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "escalation_awaiting_request_v1"
down_revision = "chat_awaited_reply_v1"
branch_labels = None
depends_on = None


def _has_column(insp: sa_inspect, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "chats", "escalation_awaiting_request"):
        op.add_column(
            "chats",
            sa.Column(
                "escalation_awaiting_request",
                sa.Boolean(),
                server_default="false",
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("chats", "escalation_awaiting_request")
