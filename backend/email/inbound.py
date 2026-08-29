"""What happens to a reply that arrives on the inbound e-mail lane.

One rule governs the whole module: **attribution decides what happens to a
reply, never whether it is delivered.** A seat holder's answer enters the chat
thread and the visitor sees it in the widget. An answer from anybody else — a
colleague without a seat, an address matching no account at all — is forwarded
to the visitor by e-mail instead. Both paths answer the customer. Refusing a
reply loses a real answer written by a real person to another real person, and
the sender is given no sign that it happened, which is why no branch here ever
does it.

The two exceptions are not refusals of an answer:

* a token that addresses nothing (never minted, or revoked) — there is no
  conversation to deliver into and no visitor address to fall back on, so
  there is nothing that could be done with the message;
* a message from the visitor themselves, or from one of our own addresses —
  forwarding that would mail somebody their own words back and invite them to
  reply again. See :func:`_is_loopback`.

**Delivery to the visitor is not optional.** Moving ``Reply-To`` onto our own
domain takes away the operator → visitor path that exists today; the onward
send here is what replaces it, and it runs on the ingested path as well as the
forwarded one.

Brevo has already separated the reply from the quoted history by the time we
see it (``ExtractedMarkdownMessage`` / ``ExtractedMarkdownSignature``), so
there are no "On … wrote:" heuristics here — the raw text part is a fallback
for the case where those fields are absent.
"""

from __future__ import annotations

import enum
import logging
import re
import uuid
from dataclasses import dataclass, field
from html import unescape
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.email.reply_lane import token_from_recipients, user_by_email
from backend.models import Chat, EscalationTicket
from backend.observability.metrics import capture_event
from backend.seats.service import user_holds_seat

logger = logging.getLogger(__name__)

#: Attachments are not carried into the thread in v1 — the transcript has no
#: place to put them and the widget has no way to render them. Their names are
#: appended to the forwarded e-mail so the visitor is not left wondering where
#: the file went; on the ingested path the operator still has them in their
#: own sent mail.
_MAX_LISTED_ATTACHMENTS = 10


class InboundOutcome(str, enum.Enum):
    """What the lane did with one inbound message."""

    #: Written into the chat thread (and mailed onward to the visitor).
    ingested = "ingested"
    #: Mailed to the visitor only — the sender holds no seat, or the request
    #: is already closed.
    forwarded = "forwarded"
    #: The visitor's own message, or one of ours, arriving back at us.
    ignored_loopback = "ignored_loopback"
    #: Nothing to deliver: an empty body once the quote was stripped.
    ignored_empty = "ignored_empty"
    #: The onward send failed on a message that was not ingested. Nothing has
    #: landed anywhere, so the caller asks the sender to retry.
    forward_failed = "forward_failed"


@dataclass(frozen=True)
class InboundReply:
    """One inbound message, normalized out of Brevo's payload."""

    token: str | None
    from_email: str
    from_name: str = ""
    subject: str = ""
    text: str = ""
    signature: str = ""
    in_reply_to: str = ""
    references: str = ""
    attachment_names: list[str] = field(default_factory=list)
    #: Brevo's own id for this message, the key redelivery is deduplicated on.
    #: Empty when the payload carried none, in which case the message is
    #: processed without a receipt — the duplicate we might write is a smaller
    #: harm than the answer we would drop by refusing it.
    provider_message_id: str = ""


@dataclass(frozen=True)
class InboundResult:
    outcome: InboundOutcome
    ticket_number: str | None = None
    #: The ticket this reply belonged to, for the delivery receipt.
    ticket_id: uuid.UUID | None = None
    #: Whether the reply's ``In-Reply-To`` / ``References`` pointed at the
    #: notification we sent. Recorded, never enforced — see
    #: :func:`_threading_matches`.
    thread_match: bool | None = None


# --------------------------------------------------------------------------
# Payload parsing
# --------------------------------------------------------------------------


def _address_list(raw: Any) -> list[str]:
    """Flatten Brevo's several spellings of an address list into strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        addr = raw.get("Address") or raw.get("address") or ""
        return [str(addr)] if addr else []
    if isinstance(raw, list):
        out: list[str] = []
        for entry in raw:
            out.extend(_address_list(entry))
        return out
    return []


_BLOCK_BREAK_RE = re.compile(
    r"(?i)</\s*(p|div|tr|li|h[1-6]|blockquote)\s*>|<\s*br\s*/?>"
)
_DROP_ELEMENT_RE = re.compile(
    r"(?is)<\s*(script|style|head)[^>]*>.*?</\s*\1\s*>"
)
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _text_from_html(html: str) -> str:
    """Readable text out of an HTML-only mail body.

    Not a parser and not trying to be: this runs only when Brevo produced no
    markdown and the message carried no plain-text part, and its whole job is
    to keep a real answer from vanishing. Block ends become newlines so the
    paragraphs of a normal reply survive, entities are decoded so the visitor
    is not shown ``&amp;nbsp;``, and everything else is dropped.
    """
    if not html.strip():
        return ""
    text = _DROP_ELEMENT_RE.sub(" ", html)
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    lines = [line.strip() for line in text.split("\n")]
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()


def _first_address(raw: Any) -> tuple[str, str]:
    """``(address, display name)`` for a single-address field."""
    if isinstance(raw, dict):
        return (
            str(raw.get("Address") or raw.get("address") or "").strip(),
            str(raw.get("Name") or raw.get("name") or "").strip(),
        )
    addrs = _address_list(raw)
    return (addrs[0].strip() if addrs else "", "")


def _header(headers: Any, name: str) -> str:
    """Case-insensitive lookup in Brevo's ``Headers`` map."""
    if not isinstance(headers, dict):
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            if isinstance(value, list):
                return " ".join(str(v) for v in value)
            return str(value)
    return ""


def parse_brevo_payload(payload: Any) -> list[InboundReply]:
    """Normalize a Brevo Inbound Parsing webhook body into replies.

    Brevo posts ``{"items": [...]}`` and may batch more than one message into a
    single call. Everything here is defensive: the body is attacker-reachable
    (the endpoint is a URL somebody may guess or a notification may leak), so a
    missing or oddly-typed field yields an empty or skipped reply rather than a
    traceback.
    """
    if isinstance(payload, dict):
        items = payload.get("items")
        raw_items = items if isinstance(items, list) else [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        return []

    replies: list[InboundReply] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        # ``Recipients`` first: it is Brevo's flattened delivered-to list, and
        # it carries our plus-address even when the operator's client put it
        # somewhere ``To`` does not show it (a Bcc, a list expansion).
        recipients = (
            _address_list(item.get("Recipients"))
            + _address_list(item.get("To"))
            + _address_list(item.get("Cc"))
        )
        from_email, from_name = _first_address(item.get("From"))
        headers = item.get("Headers")
        # Brevo hands the reply body already separated from the quoted
        # history. The raw text part is the fallback for a sender whose client
        # produced something Brevo could not split.
        text = str(item.get("ExtractedMarkdownMessage") or "").strip()
        if not text:
            text = str(item.get("RawTextBody") or "").strip()
        if not text:
            # A client that sent HTML and no plain-text alternative, from which
            # Brevo produced no markdown either. Rare, but the failure was
            # silent: an empty body is ``ignored_empty``, so a real answer
            # disappeared with nothing said to the person who wrote it. A
            # crude de-tag beats losing it.
            text = _text_from_html(str(item.get("RawHtmlBody") or ""))
        attachments = item.get("Attachments")
        names: list[str] = []
        if isinstance(attachments, list):
            for att in attachments[:_MAX_LISTED_ATTACHMENTS]:
                if isinstance(att, dict):
                    name = att.get("Name") or att.get("name")
                    if name:
                        names.append(str(name))
        replies.append(
            InboundReply(
                token=token_from_recipients(recipients),
                from_email=from_email,
                from_name=from_name,
                subject=str(item.get("Subject") or "").strip(),
                text=text,
                signature=str(item.get("ExtractedMarkdownSignature") or "").strip(),
                in_reply_to=str(
                    item.get("InReplyTo") or _header(headers, "In-Reply-To") or ""
                ).strip(),
                references=_header(headers, "References").strip(),
                attachment_names=names,
                provider_message_id=_provider_message_id(item),
            )
        )
    return replies



def _provider_message_id(item: dict) -> str:
    """Brevo's id for one inbound message, under whichever key it arrived.

    ``Uuid`` is a list in their payloads; ``MessageId`` is the RFC 5322 header
    and is the fallback because a sender controls it and two different messages
    could in principle carry the same one. Either is stable across a
    redelivery, which is all this has to be.
    """
    raw = item.get("Uuid")
    if isinstance(raw, list) and raw:
        return str(raw[0]).strip()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return str(item.get("MessageId") or "").strip()


# --------------------------------------------------------------------------
# Handling
# --------------------------------------------------------------------------


class UnknownReplyTokenError(Exception):
    """The address carried a token that matches no ticket."""


def _is_loopback(reply: InboundReply, ticket: EscalationTicket) -> bool:
    """Would acting on this message mail somebody their own words back?

    Three senders qualify: the visitor whose conversation this is, our own
    ``EMAIL_FROM``, and anything on the inbound domain itself. Forwarding any
    of them creates a ping-pong that neither side can stop — the visitor
    replies to the forward, the forward comes back here, and it is forwarded
    again. This is the one place the lane deliberately drops a message, and it
    drops nothing an operator wrote.
    """
    sender = (reply.from_email or "").strip().lower()
    if not sender:
        return True
    if ticket.user_email and sender == ticket.user_email.strip().lower():
        return True
    if settings.EMAIL_FROM and sender == settings.EMAIL_FROM.strip().lower():
        return True
    domain = (settings.inbound_email_domain or "").strip().lower()
    return bool(domain) and sender.endswith(f"@{domain}")


def _threading_matches(reply: InboundReply, ticket: EscalationTicket) -> bool | None:
    """Does the reply thread under the notification we sent?

    A cross-check, and only that. When the ticket has a ``Message-ID`` anchor
    and the reply carries ``In-Reply-To`` / ``References``, agreement is
    corroborating evidence that this really is a reply to our notification, and
    it is recorded on the way past. Disagreement is *not* a refusal: mail
    clients rewrite, drop and re-wrap these headers, a message forwarded to a
    colleague loses them entirely, and mailing lists rewrite them wholesale.
    Gating on it would silently discard real answers from perfectly ordinary
    setups — the exact failure this lane exists to prevent.

    ``None`` when there is nothing to compare.
    """
    anchor = (ticket.notification_message_id or "").strip()
    if not anchor:
        return None
    haystack = f"{reply.in_reply_to} {reply.references}".strip()
    if not haystack:
        return None
    return anchor in haystack


def _forward_body(reply: InboundReply) -> str:
    """What the visitor receives: the answer as written, and nothing added.

    Deliberately not framed with copy of ours. Any sentence we wrapped around
    it would be one more visitor-facing string to localize, and the operator
    has already written the message they meant the customer to read. The
    signature block goes back on — Brevo split it off, and on an e-mail (unlike
    in a chat bubble) it is how the customer knows who answered them.
    """
    parts = [reply.text]
    if reply.signature:
        parts.append(reply.signature)
    if reply.attachment_names:
        parts.append(
            "Attachments (not included): " + ", ".join(reply.attachment_names)
        )
    return "\n\n".join(p for p in parts if p)


def _forward_to_visitor(
    reply: InboundReply, ticket: EscalationTicket, db: Session
) -> bool:
    """Mail the answer to the visitor. Returns whether it went out.

    ``Reply-To`` is the tenant's support inbox, never our token address: a
    visitor who answers by mail must reach the humans, and pointing them back
    at the lane would feed their message into :func:`_is_loopback` and drop it.
    """
    from backend.escalation.service import (
        _is_valid_email,
        _safe_ticket_question,
        _send_email_off_loop,
        _support_inbox_recipient,
    )

    if not _is_valid_email(ticket.user_email):
        logger.info(
            "email_lane_forward_skipped_no_visitor_address ticket=%s",
            ticket.ticket_number,
        )
        return False

    tenant = ticket.tenant
    support_inbox = _support_inbox_recipient(tenant, db) if tenant is not None else None
    subject = reply.subject.strip()
    if not subject:
        preview = _safe_ticket_question(ticket).replace("\n", " ").strip()[:60]
        subject = f"Re: [{ticket.ticket_number}] {preview}".rstrip(" —-")

    try:
        # Off the event loop: the webhook runs the sync work in a ``run_sync``
        # greenlet on the loop thread, where a 10 s Brevo call would stall
        # every in-flight chat turn.
        sent = _send_email_off_loop(
            ticket.user_email,
            subject,
            _forward_body(reply),
            reply_to=support_inbox,
        )
    except Exception:
        logger.warning(
            "email_lane_forward_exception ticket=%s", ticket.ticket_number, exc_info=True
        )
        return False
    return sent is not None


def _ingest(
    reply: InboundReply,
    ticket: EscalationTicket,
    chat: Chat,
    user_id: uuid.UUID,
    db: Session,
) -> None:
    """Write the answer into the chat thread through the one operator seam."""
    from backend.operator.service import (
        OperatorActor,
        OperatorChannel,
        ingest_from_operator,
    )

    ingest_from_operator(
        db,
        chat=chat,
        tenant_id=ticket.tenant_id,
        # The signature is left out here on purpose: it is mail furniture, and
        # a "Best regards / Ann / Support" block rendered inside a chat bubble
        # is noise the visitor did not ask for. The forwarded e-mail keeps it.
        text=reply.text,
        actor=OperatorActor(channel=OperatorChannel.email, user_id=user_id),
    )


def handle_inbound_reply(reply: InboundReply, db: Session) -> InboundResult:
    """Route one inbound reply. Never raises for ordinary content.

    Returns ``None``-free outcomes the caller turns into a status code:
    everything except :attr:`InboundOutcome.forward_failed` is a 200, because
    the message has been dealt with as well as it can be.
    """
    from backend.email.reply_lane import ticket_for_token
    from backend.escalation.service import ACTIVE_TICKET_STATUSES

    ticket = ticket_for_token(reply.token or "", db)
    if ticket is None:
        # Refused: a token addressing nothing gives us neither a conversation
        # to write into nor a visitor to forward to.
        raise UnknownReplyTokenError()

    if not reply.text.strip():
        return InboundResult(
            InboundOutcome.ignored_empty, ticket.ticket_number, ticket.id
        )

    if _is_loopback(reply, ticket):
        logger.info("email_lane_loopback_dropped ticket=%s", ticket.ticket_number)
        return InboundResult(
            InboundOutcome.ignored_loopback, ticket.ticket_number, ticket.id
        )

    thread_match = _threading_matches(reply, ticket)

    user = user_by_email(reply.from_email, tenant_id=ticket.tenant_id, db=db)
    seated = user is not None and user_holds_seat(user_id=user.id, db=db)
    chat: Chat | None = (
        db.query(Chat).filter(Chat.id == ticket.chat_id).first()
        if ticket.chat_id
        else None
    )
    live_request = ticket.status in ACTIVE_TICKET_STATUSES

    # A seated sender writing into a request that is still live and still has
    # its conversation is the whole point of the lane. Everything else — no
    # seat, no account, a closed request, a chat that has been deleted — takes
    # the forward path, which answers the customer just the same.
    if seated and chat is not None and live_request:
        _ingest(reply, ticket, chat, user.id, db)
        # Mandatory, not best-effort in intent: this is what replaces the
        # direct operator → visitor path that ``Reply-To`` used to provide.
        # It is best-effort in *consequence* only — the answer is already in
        # the thread and visible in the widget, so a failed send must not undo
        # it or ask for a retry that would write the message twice.
        if not _forward_to_visitor(reply, ticket, db):
            logger.warning(
                "email_lane_forward_failed_after_ingest ticket=%s", ticket.ticket_number
            )
            _capture(
                "email_lane.forward_failed",
                ticket,
                properties={"ingested": True, "thread_match": thread_match},
            )
        _capture(
            "email_lane.reply_ingested",
            ticket,
            properties={"thread_match": thread_match},
        )
        return InboundResult(
            InboundOutcome.ingested, ticket.ticket_number, ticket.id, thread_match
        )

    if not _forward_to_visitor(reply, ticket, db):
        # Nothing has landed anywhere, so a retry cannot duplicate anything.
        # Ask for one rather than swallowing a real answer.
        _capture(
            "email_lane.forward_failed",
            ticket,
            properties={"ingested": False, "thread_match": thread_match},
        )
        return InboundResult(
            InboundOutcome.forward_failed, ticket.ticket_number, ticket.id, thread_match
        )
    _capture(
        "email_lane.reply_forwarded",
        ticket,
        properties={
            # The actual reason, not the first plausible one. The earlier
            # expression reported "not_live" for a seated operator whose chat
            # had been deleted, which is a different failure with a different
            # fix and was invisible behind that label.
            "reason": _forward_reason(seated=seated, chat=chat, live=live_request),
            "thread_match": thread_match,
        },
    )
    return InboundResult(
        InboundOutcome.forwarded, ticket.ticket_number, ticket.id, thread_match
    )


def _forward_reason(*, seated: bool, chat: Chat | None, live: bool) -> str:
    """Why this reply took the forward path rather than entering the thread.

    Ordered by what somebody reading the metric would act on first: no seat is
    a billing conversation, a closed request is normal, and a missing chat is a
    bug worth knowing about separately from either.
    """
    if not seated:
        return "no_seat"
    if not live:
        return "not_live"
    if chat is None:
        return "no_chat"
    return "unknown"


def _capture(event: str, ticket: EscalationTicket, *, properties: dict) -> None:
    """Product metric, best-effort. Never carries the sender's address."""
    try:
        tenant_id = str(ticket.tenant_id)
        capture_event(
            event,
            distinct_id=tenant_id,
            tenant_id=tenant_id,
            properties={"ticket_number": ticket.ticket_number, **properties},
            groups={"tenant": tenant_id},
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("email lane metric failed", exc_info=True)
