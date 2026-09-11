"""Operator handoff — a repeat human request re-enters the queue.

Adds ``escalation_tickets.requested_again_at``.

The inbox treats a request as waiting while no operator has written in the
session since it was raised. A visitor who was answered, handed back to the
bot and then asked for a human again reuses the same ticket (one active
ticket per chat), so nothing moved and the request never came back into the
queue. This stamp records the repeat; the queue reads "since it was raised"
as ``coalesce(requested_again_at, created_at)``.

Revision ID: escalation_requested_again_v1
Revises: msg_embedding_not_null_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "escalation_requested_again_v1"
down_revision = "msg_embedding_not_null_v1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    try:
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False
    return column in cols


def upgrade() -> None:
    if not _has_column("escalation_tickets", "requested_again_at"):
        op.add_column(
            "escalation_tickets",
            sa.Column("requested_again_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    # Documentation only — downgrade is never executed against a shared or
    # production database (see project CLAUDE.md).
    op.drop_column("escalation_tickets", "requested_again_at")
