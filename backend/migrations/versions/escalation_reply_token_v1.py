"""The per-ticket credential the inbound e-mail lane is addressed by

``escalation_tickets.reply_token`` — nullable, unique. Non-NULL means "a reply
sent to ``reply+<token>@<inbound domain>`` resolves to this ticket"; NULL means
one was never minted, which is every ticket a seatless workspace raises.

``reply_token_revoked_at`` carries the withdrawal. Revocation stamps rather
than erases, because erasing left a late reply unattributable to any ticket —
no conversation, and no visitor address to forward to, so a real answer was
dropped in silence. A stamped token stops being a way into the conversation at
once (the ticket is closed, so the lane forwards) and stops resolving at all
once the grace window in ``backend/email/reply_lane.py`` has passed.

**Why a stored random token and not a derived one.** The alternative was to
derive the address from the ticket id — ``HMAC(server_secret, ticket.id)`` —
which needs no column at all. It was rejected on revocation: a derived token
cannot be withdrawn for one conversation, only for all of them at once by
rotating the secret, and the whole security argument for putting a bearer
credential inside an e-mail is that its blast radius is one conversation and
that it can be killed. A nullable column on a low-volume table is the cheaper
half of that trade.

**Why unique.** The token *is* the lookup key for an inbound reply. A unique
index makes the lookup an index scan and makes a collision a write error
rather than a silently ambiguous read. NULLs do not collide in either
PostgreSQL or SQLite, so every ticket without a token coexists happily.

This revision was written as a merge point for the two heads that
``member_removal_signatures_v1`` had branched into. Those were merged on main
instead, by ``merge_seats_low_confidence_v1``, after the split took production
down on 2026-08-28 — so this one now sits behind that merge as an ordinary
single-parent revision. Anything long-lived alongside main wants its
``down_revision`` re-checked against ``alembic heads`` immediately before
merge, not when the branch was opened: that is the same check this branch
failed twice.

Revision ID: escalation_reply_token_v1
Revises: low_confidence_second_attempt_v1, operator_seats_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql

revision = "escalation_reply_token_v1"
down_revision = "merge_seats_low_confidence_v1"
branch_labels = None
depends_on = None

_TABLE = "escalation_tickets"
_COLUMN = "reply_token"
_REVOKED = "reply_token_revoked_at"
_INDEX = "ix_escalation_tickets_reply_token"
_RECEIPTS = "inbound_email_receipts"
_RECEIPTS_INDEX = "ix_inbound_email_receipts_provider_message_id"


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def _has_index(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def upgrade() -> None:
    insp = sa_inspect(op.get_bind())

    if not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=64), nullable=True))
    if not _has_column(insp, _TABLE, _REVOKED):
        op.add_column(_TABLE, sa.Column(_REVOKED, sa.DateTime(), nullable=True))
    if not _has_index(insp, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=True)

    # What the lane has already acted on. Brevo re-delivers the whole webhook
    # body on any non-2xx, so without this a batch carrying one written reply
    # and one failed send could only choose which way to be wrong.
    if _RECEIPTS not in insp.get_table_names():
        op.create_table(
            _RECEIPTS,
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("provider_message_id", sa.String(length=998), nullable=False),
            sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        op.create_index(
            _RECEIPTS_INDEX, _RECEIPTS, ["provider_message_id"], unique=True
        )


def downgrade() -> None:
    # Documented for completeness; never run against a shared or production
    # database (see the global Alembic rules). Dropping the column revokes
    # every outstanding reply address at once — replies already in flight to
    # ``reply+<token>@…`` would be refused, and the answers they carry lost.
    insp = sa_inspect(op.get_bind())

    if _RECEIPTS in insp.get_table_names():
        op.drop_table(_RECEIPTS)
    if _has_index(insp, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column(insp, _TABLE, _REVOKED):
        op.drop_column(_TABLE, _REVOKED)
    if _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
