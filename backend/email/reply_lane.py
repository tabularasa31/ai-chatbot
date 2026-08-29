"""Addressing for the inbound e-mail lane: minting, parsing and revoking.

The lane exists so that an operator who never opens the dashboard can answer a
customer from their mailbox and have that answer land in the conversation. All
of it hangs off one address:

    reply+<token>@<INBOUND_EMAIL_DOMAIN>

An escalation notification carries that as ``Reply-To`` when the workspace
holds a seat; the operator hits Reply; Brevo Inbound Parsing posts the message
to :mod:`backend.email.routes`; the token identifies the ticket, and through it
the chat.

**A workspace with no seat sees none of this.** ``Reply-To`` stays the
visitor's own address, the reply goes straight to them exactly as it does
today, and no token is ever minted. Receiving a reply we would then decline to
use would be worse than not receiving it, because the operator is given no sign
that anything went wrong.

**The token is a bearer credential inside an e-mail.** Anyone who obtains the
notification can write into that one visitor's chat — that is the honest
statement of the exposure, and it is bounded on three sides: the blast radius
is a single conversation, the token is revocable (:func:`revoke_reply_token`
clears it, and every terminal ticket transition does), and the ``From`` of
whatever arrives is recorded on the way in. Defence is layered rather than
single: the webhook path carries a secret, the address carries the token, and
the reply's ``In-Reply-To`` is cross-checked against the notification's
``Message-ID``.

Sender domain is deliberately *not* part of the defence. Outsourced support,
agencies and personal mail accounts are ordinary ways for a real operator to
answer; a domain rule would refuse more real answers than it would stop
forgeries.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta
from email.utils import parseaddr

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models import EscalationTicket, User
from backend.models.base import _utcnow
from backend.seats.service import tenant_has_any_seat

logger = logging.getLogger(__name__)

#: Local part before the ``+token`` suffix. A constant rather than a setting:
#: the mailbox is ours, nobody configures it per tenant, and the inbound
#: parser routes the whole domain to one webhook regardless.
REPLY_LOCAL_PART = "reply"

#: 32 random bytes, URL-safe base64 — 43 characters, all of them valid in an
#: e-mail local part, and far past guessing. Sized to leave room inside the
#: column's 64 characters for the day this grows a prefix.
_TOKEN_BYTES = 32


def lane_is_wired() -> bool:
    """Is the inbound lane configured at all?

    Presence of the webhook secret, the same test ``send_email`` applies to
    ``BREVO_API_KEY``: without it Brevo has nowhere to deliver to, so minting a
    reply address would advertise a mailbox whose contents reach nobody. Not a
    feature flag — there is no configuration in which the lane is built,
    reachable and switched off.
    """
    return bool(settings.inbound_email_secret and settings.inbound_email_domain)


def reply_address(token: str) -> str:
    return f"{REPLY_LOCAL_PART}+{token}@{settings.inbound_email_domain}"


#: How long a closed ticket's reply address still resolves. It buys nothing
#: inside the conversation — a closed ticket always takes the forward path —
#: only the ability to put a late answer in front of the visitor instead of
#: discarding it. Long enough for an operator who read the notification
#: yesterday, short enough that a leaked notification is not a permanent way to
#: mail somebody.
REVOKED_TOKEN_GRACE = timedelta(days=7)


def mint_reply_token(ticket: EscalationTicket, db: Session) -> str:
    """Return this ticket's token, minting and flushing one if it has none.

    Flushed rather than merely staged because the notification is sent before
    the caller's transaction ends: an address whose token is still only in the
    session would be advertised to an operator who could reply before it was
    ever written, and their answer would be refused as unknown.

    Idempotent — a ticket that already holds a token keeps it, so re-notifying
    (the abandoned-claim bounce, a repeat escalation) does not invalidate the
    address on notifications already sitting in someone's inbox.
    """
    if not ticket.reply_token:
        ticket.reply_token = secrets.token_urlsafe(_TOKEN_BYTES)
    if ticket.reply_token_revoked_at is not None:
        # The ticket has been reopened. Minting is what puts this address into
        # a notification going out right now, so leaving yesterday's revocation
        # stamped would advertise an address that answers 404 — the operator
        # replies to today's mail and their answer disappears, which is the
        # exact failure the stamp was introduced to prevent.
        ticket.reply_token_revoked_at = None
        db.add(ticket)
        db.flush()
    return ticket.reply_token


def revoke_reply_token(ticket: EscalationTicket) -> None:
    """Close this ticket's reply address. Staged only; the caller commits.

    Called on every terminal ticket transition. A closed conversation is not a
    place a stranger holding an old notification should be able to write into,
    and "revocable" has to mean something an operational human can actually do.

    Stamped rather than erased, and the difference matters. Erasing the token
    made a late reply unattributable to anything: the ticket could no longer be
    found, so there was no visitor address to forward to and the operator's
    answer was dropped with nothing said to them. Tickets close on their own —
    the sweeper resolves stale ones — so "late" here is an operator answering a
    notification from this morning, which is ordinary rather than exotic.

    A stamped token stops being a way *into the conversation* immediately: the
    ticket is no longer active, so :func:`~backend.email.inbound.handle_inbound_reply`
    takes the forward path and mails the answer to the visitor. It stops
    working altogether once :data:`REVOKED_TOKEN_GRACE` has passed.
    """
    if ticket.reply_token and ticket.reply_token_revoked_at is None:
        ticket.reply_token_revoked_at = _utcnow()


def escalation_reply_to(ticket: EscalationTicket, db: Session) -> str | None:
    """What ``Reply-To`` this ticket's notifications should carry.

    The seat branch, and the first caller of
    :func:`~backend.seats.service.tenant_has_any_seat`. With a seat somewhere
    in the workspace the reply can come back through us and be put in front of
    the visitor in the widget; with none it must go straight to the visitor's
    mailbox, or the answer lands nowhere.

    Falls back to the visitor's address on any failure to mint — an
    unreachable database or a token collision must degrade to today's
    behaviour, never to a notification with no way to reply to it.
    """
    if not lane_is_wired():
        return ticket.user_email
    try:
        if not tenant_has_any_seat(tenant_id=ticket.tenant_id, db=db):
            return ticket.user_email
        return reply_address(mint_reply_token(ticket, db))
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "reply_lane_mint_failed ticket=%s", ticket.ticket_number, exc_info=True
        )
        return ticket.user_email


def token_from_recipients(addresses: list[str]) -> str | None:
    """Pull our token out of the addresses an inbound message was sent to.

    Brevo routes the whole inbound domain to one webhook, so the recipient list
    is where the ticket identity lives. Anything on another domain, or without
    the ``reply+`` prefix, is not ours — a Cc to a colleague, the tenant's own
    inbox — and is skipped rather than treated as a malformed token.
    """
    domain = (settings.inbound_email_domain or "").strip().lower()
    if not domain:
        return None
    prefix = f"{REPLY_LOCAL_PART}+"
    for raw in addresses:
        _name, addr = parseaddr(raw or "")
        if "@" not in addr:
            continue
        local, _, host = addr.rpartition("@")
        if host.strip().lower() != domain:
            continue
        local = local.strip()
        # The local part is compared case-sensitively after the prefix: the
        # token is base64url and ``a`` and ``A`` are different tokens.
        if local[: len(prefix)].lower() != prefix:
            continue
        token = local[len(prefix) :]
        if token:
            return token
    return None


def ticket_for_token(token: str, db: Session) -> EscalationTicket | None:
    """The ticket this token addresses, or ``None`` if it addresses nothing.

    ``None`` covers every refusal case at once — a token we never minted, one
    revoked longer ago than :data:`REVOKED_TOKEN_GRACE`, and one belonging to a
    ticket that has since been deleted — because none of them is
    distinguishable to the sender and none of them should be.

    A token revoked *within* the grace window still resolves. It cannot write
    into the conversation — the ticket is closed, so the caller forwards — but
    it still names a visitor to deliver the answer to, which is the whole point
    of resolving it at all.
    """
    if not token:
        return None
    ticket = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.reply_token == token)
        .first()
    )
    if ticket is None:
        return None
    revoked_at = ticket.reply_token_revoked_at
    if revoked_at is not None and _utcnow() - revoked_at > REVOKED_TOKEN_GRACE:
        return None
    return ticket


def user_by_email(email: str, *, tenant_id: uuid.UUID, db: Session) -> User | None:
    """The workspace member who owns this address, matched case-insensitively.

    Scoped to the ticket's own workspace: an address that belongs to a member
    of *another* tenant is a stranger here, and must attribute to nobody rather
    than to a person who has no business in this conversation.
    """
    candidate = (email or "").strip()
    if not candidate:
        return None
    return (
        db.query(User)
        .filter(
            User.tenant_id == tenant_id,
            func.lower(User.email) == candidate.lower(),
        )
        .first()
    )
