"""Add last_reply_was_low_confidence to chats.

Tracks whether the immediately preceding assistant reply was answered from
weak retrieval, so a handoff is offered only on a second consecutive weak turn
instead of on the first one.

Revision ID: low_confidence_second_attempt_v1
Revises: member_removal_signatures_v1
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "low_confidence_second_attempt_v1"
down_revision = "member_removal_signatures_v1"
branch_labels = None
depends_on = None


def _has_column(insp: sa_inspect, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "chats", "last_reply_was_low_confidence"):
        op.add_column(
            "chats",
            sa.Column(
                "last_reply_was_low_confidence",
                sa.Boolean(),
                server_default="false",
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("chats", "last_reply_was_low_confidence")
