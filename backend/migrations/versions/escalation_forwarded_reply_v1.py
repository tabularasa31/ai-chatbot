"""Stamp on ``escalation_tickets`` when a mailed reply was forwarded to the
visitor without entering the conversation.

``forwarded_reply_at`` / ``forwarded_reply_from`` record the newest inbound
reply that took the forward path — a sender holding no seat in the workspace,
or writing into a request that had already closed. The answer reached the
visitor by mail, outside the product, and until now the inbox showed the
request as never answered. Two columns rather than a table: the inbox needs
the latest fact, not a history.

Idempotent: ``IF NOT EXISTS`` semantics via the inspector, so a partial
earlier run does not fail the deploy.

Revision ID: escalation_forwarded_reply_v1
Revises: escalation_requested_again_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "escalation_forwarded_reply_v1"
down_revision = "escalation_requested_again_v1"
branch_labels = None
depends_on = None

_TABLE = "escalation_tickets"
_AT = "forwarded_reply_at"
_FROM = "forwarded_reply_from"


def _has_column(insp, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(_TABLE))


def upgrade() -> None:
    insp = sa_inspect(op.get_bind())
    if not _has_column(insp, _AT):
        op.add_column(_TABLE, sa.Column(_AT, sa.DateTime(), nullable=True))
    if not _has_column(insp, _FROM):
        op.add_column(_TABLE, sa.Column(_FROM, sa.String(length=255), nullable=True))


def downgrade() -> None:
    insp = sa_inspect(op.get_bind())
    if _has_column(insp, _FROM):
        op.drop_column(_TABLE, _FROM)
    if _has_column(insp, _AT):
        op.drop_column(_TABLE, _AT)
