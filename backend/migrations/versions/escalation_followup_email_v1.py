"""Escalation follow-up update emails: ticket threading + debounce metadata.

Adds three columns on ``escalation_tickets`` so subsequent user turns after a
support handoff can be forwarded to the same email thread as a reply (rather
than vanishing into the dashboard the tenant has no access to):

  * ``notification_message_id`` — RFC 5322 ``Message-ID`` of the original
    notification email, captured from the Brevo API response. Used as the
    target of ``In-Reply-To`` / ``References`` in update emails so support's
    mail client groups them under the same conversation.
  * ``last_notified_at`` — last time we sent a notify to support. Drives a
    synchronous debounce so several user keystrokes within ~60s collapse into
    a single update email.
  * ``last_notified_message_id`` — Message.id of the last user turn included
    in a notify. The next update email selects only ``Message.id > this`` to
    keep the body to the actual delta.

Revision ID: escalation_followup_email_v1
Revises: gap_faq_workflow_v1
Create Date: 2026-05-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "escalation_followup_email_v1"
down_revision = "gap_faq_workflow_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("escalation_tickets")}

    if "notification_message_id" not in cols:
        op.add_column(
            "escalation_tickets",
            sa.Column("notification_message_id", sa.String(length=998), nullable=True),
        )
    if "last_notified_at" not in cols:
        op.add_column(
            "escalation_tickets",
            sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        )
    if "last_notified_message_id" not in cols:
        op.add_column(
            "escalation_tickets",
            sa.Column("last_notified_message_id", PG_UUID(as_uuid=True), nullable=True),
        )


def downgrade() -> None:
    # Documented for completeness only — never run against shared DBs.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("escalation_tickets")}
    for name in ("last_notified_message_id", "last_notified_at", "notification_message_id"):
        if name in cols:
            op.drop_column("escalation_tickets", name)
