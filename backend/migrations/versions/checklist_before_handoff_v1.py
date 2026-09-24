"""Add last_reply_was_checklist to chats.

Tracks whether the immediately preceding assistant reply handed the user a
checklist of steps to run, so a bare request to forward the conversation gets
one re-ask for the result before a ticket is created.

Revision ID: checklist_before_handoff_v1
Revises: 6f2031d39b77
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "checklist_before_handoff_v1"
down_revision = "6f2031d39b77"
branch_labels = None
depends_on = None


def _has_column(insp: sa_inspect, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "chats", "last_reply_was_checklist"):
        op.add_column(
            "chats",
            sa.Column(
                "last_reply_was_checklist",
                sa.Boolean(),
                server_default="false",
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("chats", "last_reply_was_checklist")
