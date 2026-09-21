"""Visitor read cursor and unread-reply mail marker on chats

Two nullable UUID columns on ``chats``:
  - ``visitor_read_message_id``        — newest message the visitor has had on
                                          screen (widget panel open, tab visible)
  - ``unread_reply_mailed_message_id`` — newest operator reply mailed to the
                                          visitor because it went unread

Idempotent: each step inspects live state before acting.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "visitor_read_cursor_v1"
down_revision = "legacy_closed_chats_marker_v1"
branch_labels = None
depends_on = None

_COLUMNS = ("visitor_read_message_id", "unread_reply_mailed_message_id")


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    for name in _COLUMNS:
        if not _has_column(insp, "chats", name):
            op.add_column("chats", sa.Column(name, PG_UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    for name in _COLUMNS:
        if _has_column(insp, "chats", name):
            op.drop_column("chats", name)
