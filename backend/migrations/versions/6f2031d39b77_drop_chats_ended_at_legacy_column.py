"""drop chats ended_at legacy column

``chats.ended_at`` tracked a visitor-initiated close state that no longer
exists; ``legacy_closed_chats_marker_v1`` already backfilled every value into
``session_ended_event_at`` and nothing reads the column since. Guarded with
an inspector check so a repeat run (or a DB that never had the column) is a
no-op.

Revision ID: 6f2031d39b77
Revises: c832aa041ddb
Create Date: 2026-09-22 21:42:31.178249

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6f2031d39b77'
down_revision = 'c832aa041ddb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("chats")}
    if "ended_at" in columns:
        op.drop_column("chats", "ended_at")


def downgrade() -> None:
    # Documented no-op: never run against shared/production data. Recreating
    # the column would not restore the values dropped by upgrade().
    pass
