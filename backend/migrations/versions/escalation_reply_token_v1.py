"""The per-ticket credential the inbound e-mail lane is addressed by

``escalation_tickets.reply_token`` — nullable, unique. Non-NULL means "a reply
sent to ``reply+<token>@<inbound domain>`` may act on this ticket"; NULL means
the token has been revoked (or was never minted, which is every ticket a
seatless workspace raises).

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

This revision is also a **merge point**. ``member_removal_signatures_v1``
branched into ``low_confidence_second_attempt_v1`` and ``operator_seats_v1``
and both were left as heads, so ``alembic upgrade head`` — what the Railway
release step runs — fails with "Multiple head revisions are present". Merging
them here is not housekeeping bolted onto a feature: this migration has to
name a ``down_revision``, and either choice on its own would have left the
tree with two heads still.

Revision ID: escalation_reply_token_v1
Revises: low_confidence_second_attempt_v1, operator_seats_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "escalation_reply_token_v1"
down_revision = ("low_confidence_second_attempt_v1", "operator_seats_v1")
branch_labels = None
depends_on = None

_TABLE = "escalation_tickets"
_COLUMN = "reply_token"
_INDEX = "ix_escalation_tickets_reply_token"


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def _has_index(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def upgrade() -> None:
    insp = sa_inspect(op.get_bind())

    if not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=64), nullable=True))
    if not _has_index(insp, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=True)


def downgrade() -> None:
    # Documented for completeness; never run against a shared or production
    # database (see the global Alembic rules). Dropping the column revokes
    # every outstanding reply address at once — replies already in flight to
    # ``reply+<token>@…`` would be refused, and the answers they carry lost.
    insp = sa_inspect(op.get_bind())

    if _has_index(insp, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
