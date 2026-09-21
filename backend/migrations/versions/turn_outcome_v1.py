"""Add messages.turn_outcome for analytics reporting

Nullable VARCHAR(32) column on ``messages`` holding a ``TurnOutcome`` value
(answered / unanswered / filtered / escalation). No DB-level enum type — same
style as ``inbound_email_receipts.outcome``. NULL means a row written before
this column existed.

Idempotent: inspects live state before acting.

Revision ID: turn_outcome_v1
Revises: visitor_read_cursor_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "turn_outcome_v1"
down_revision = "f8df9e78d4a5"
branch_labels = None
depends_on = None

_TABLE = "messages"
_COLUMN = "turn_outcome"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    if not any(c["name"] == _COLUMN for c in insp.get_columns(_TABLE)):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    if any(c["name"] == _COLUMN for c in insp.get_columns(_TABLE)):
        op.drop_column(_TABLE, _COLUMN)
