"""Retire ``chats.ended_at`` from the sweeper: backfill the marker, narrow the index.

The removed closed-chat state emitted ``chat_session_ended`` at the moment
the visitor said "no" without stamping the sweeper's marker; the sweeper
skipped those rows by ``ended_at`` instead. Now that nothing reads
``ended_at``, the sweeper would report each of them a second time. Stamping
the marker with the legacy close time keeps the event at-most-once.

``ix_chats_sweeper_pending`` was built ``WHERE session_ended_event_at IS
NULL AND ended_at IS NULL``; the sweeper's query no longer carries the
second predicate, so the planner could not use the index. Rebuilt on the
marker alone.

Idempotent: only rows without a marker are touched and the index is dropped
before it is recreated. Downgrade is a documented no-op — the
marker is analytics-only and harmless to keep, and the wider index still
serves the old query.

Revision ID: legacy_closed_chats_marker_v1
Revises: escalation_forwarded_reply_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "legacy_closed_chats_marker_v1"
down_revision = "escalation_forwarded_reply_v1"
branch_labels = None
depends_on = None

_INDEX = "ix_chats_sweeper_pending"


def upgrade() -> None:
    op.execute(
        "UPDATE chats SET session_ended_event_at = ended_at "
        "WHERE ended_at IS NOT NULL AND session_ended_event_at IS NULL"
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if any(ix["name"] == _INDEX for ix in sa.inspect(bind).get_indexes("chats")):
        op.drop_index(_INDEX, table_name="chats")
    op.create_index(
        _INDEX,
        "chats",
        ["updated_at"],
        postgresql_where=sa.text("session_ended_event_at IS NULL"),
    )


def downgrade() -> None:
    pass
