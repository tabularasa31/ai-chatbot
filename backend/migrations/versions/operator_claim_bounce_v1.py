"""Operator handoff — the once-per-ticket cap on the abandoned-claim bounce.

Adds ``escalation_tickets.claim_bounced_at``.

Phase 0 gives a claimed ticket the status ``in_progress``. When an operator
claims a request and never writes a word, the sweeper bounces it back to
``open`` and re-notifies support. That notification is an outbound e-mail, so
it needs a durable "already sent" record — a second claim that is likewise
abandoned must not send a second one.

A timestamp rather than a boolean: it costs the same, and it lets the inbox
and any later triage see *when* the request was dropped rather than only that
it was.

Revision ID: operator_claim_bounce_v1
Revises: operator_handoff_phase0_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "operator_claim_bounce_v1"
down_revision = "operator_handoff_phase0_v1"
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
    if not _has_column("escalation_tickets", "claim_bounced_at"):
        op.add_column(
            "escalation_tickets",
            sa.Column("claim_bounced_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    # Documentation only — downgrade is never executed against a shared or
    # production database (see project CLAUDE.md). Dropping this column would
    # uncap the bounce notification: every already-bounced ticket becomes
    # eligible again the moment it is re-claimed and re-abandoned.
    op.drop_column("escalation_tickets", "claim_bounced_at")
