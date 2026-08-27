"""Escalation ticket orchestration (FI-ESC)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.util import await_only

from backend.chat.language import resolve_language_context
from backend.chat.pii import redact, redact_for_egress, redact_text
from backend.contact_sessions.service import sync_user_session_identity
from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client
from backend.core.openai_retry import async_call_openai_with_retry
from backend.email.service import send_email
from backend.models import (
    Chat,
    EscalationPriority,
    EscalationStatus,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    PiiEvent,
    PiiEventDirection,
    Tenant,
    TenantProfile,
    User,
)
from backend.models.base import _utcnow
from backend.observability.cache_metrics import record_hit, record_miss
from backend.observability.metrics import capture_event
from backend.privacy_config import public_redaction_config_dict
from backend.support_config import public_support_config_dict

logger = logging.getLogger(__name__)

ESCALATION_THRESHOLD = 0.45

_CLARIFY_KEY = "escalation_followup_clarify"

_HUMAN_REQUEST_TIMEOUT = 3.0
_HUMAN_REQUEST_CACHE_TTL = 5 * 60
_HUMAN_REQUEST_CACHE_MAX = 2048


@dataclass(frozen=True)
class HumanRequestResult:
    """Outcome of the human-request classifier for a single user message.

    ``human_request`` — the user wants to be handed off to a person this turn.
    ``message_has_request_content`` — this message states a concrete problem or
    question that support could act on (as opposed to a bare greeting /
    availability ping / "connect me to a human" with no substance).

    The two are independent: an availability ping ("are you there?") is
    ``human_request=True, message_has_request_content=False``; a plain product
    question with no handoff ask is ``human_request=False,
    message_has_request_content=True``.
    """

    human_request: bool
    message_has_request_content: bool

    # Preserve the historical ``bool``-like contract at call sites and in tests
    # that still treat the result as truthy iff a human was requested.
    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.human_request


class _LockedTTLCache:
    """Thread-safe TTL cache for classifier results.

    The intent classifiers are now coroutines on the event loop, but the cache
    is process-global and may still be reached from worker threads (tests,
    sync tooling), so every operation — including the compound eviction scan —
    runs under a lock to avoid "dict changed size during iteration". All ops
    are in-memory and cheap, so holding the lock on the loop is fine.
    Hits/misses are reported under ``name``.
    """

    def __init__(self, *, name: str, ttl: float, maxsize: int) -> None:
        self._name = name
        self._ttl = ttl
        self._max = maxsize
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                record_miss(self._name)
                return None
            expires_at, result = item
            if time.time() > expires_at:
                self._data.pop(key, None)
                record_miss(self._name)
                return None
            record_hit(self._name)
            return result

    def set(self, key: str, result: Any) -> None:
        now = time.time()
        with self._lock:
            if len(self._data) >= self._max and key not in self._data:
                expired = [k for k, v in self._data.items() if now > v[0]]
                for k in expired:
                    self._data.pop(k, None)
                if len(self._data) >= self._max:
                    oldest = min(self._data.items(), key=lambda x: x[1][0])[0]
                    self._data.pop(oldest, None)
            self._data[key] = (now + self._ttl, result)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_human_request_cache = _LockedTTLCache(
    name="human_request", ttl=_HUMAN_REQUEST_CACHE_TTL, maxsize=_HUMAN_REQUEST_CACHE_MAX
)


def _tenant_optional_entity_types(tenant: Tenant | None) -> set[str] | None:
    if not tenant:
        return None
    raw = tenant.settings if isinstance(tenant.settings, dict) else None
    cfg = public_redaction_config_dict(raw)
    return set(cfg["optional_entity_types"])


def _safe_message_content(
    message: Message, optional_entity_types: set[str] | None = None
) -> str:
    """Stored message text, fully masked for anything leaving the platform."""
    return redact_for_egress(message.content, optional_entity_types=optional_entity_types)


def _safe_ticket_question(
    ticket: EscalationTicket, optional_entity_types: set[str] | None = None
) -> str:
    """Stored ticket question, fully masked for anything leaving the platform."""
    return redact_for_egress(
        ticket.primary_question, optional_entity_types=optional_entity_types
    )


# Entity types left visible in the outbound support email. A support agent
# replies to the end user and needs the email address and IP the user reported
# to actually resolve the ticket, so the email body is rendered from the stored
# original text with these two unmasked. Every other egress (OpenAI requests,
# email subject lines, transcript fallbacks) masks them — this exemption is
# scoped to the support email body.
_SUPPORT_EMAIL_VISIBLE_ENTITY_TYPES = frozenset({"EMAIL", "IP"})


def _support_email_text(text: str | None) -> str:
    """Redact original text for the support email, keeping EMAIL and IP visible.

    Must be given *original* (un-redacted) text — placeholders cannot be
    reversed. Everything except EMAIL and IP stays masked (phones, cards,
    passwords, API keys, identity documents, tokenised URLs).
    """
    if not text:
        return text or ""
    return redact_text(text, disabled_entity_types=_SUPPORT_EMAIL_VISIBLE_ENTITY_TYPES)


def _email_ticket_question(ticket: EscalationTicket) -> str:
    """Ticket question for the support email body, EMAIL/IP left visible."""
    return _support_email_text(ticket.primary_question)


def _email_message_content(message: Message) -> str:
    """Transcript message content for the support email body.

    Only *user-authored* turns get the EMAIL/IP-visible treatment — support
    needs the contact address and IP the user reported. Assistant turns are
    fully redacted: the bot may have echoed a tenant/support address or
    infrastructure IP from the knowledge base, and those must not be un-masked
    into an email the support agent's client can quote back to the end user.
    """
    if message.role != MessageRole.user:
        return _safe_message_content(message)
    return _support_email_text(message.content)


def should_escalate(
    best_similarity_score: float | None,
    chunk_count: int,
    *,
    trigger_override: EscalationTrigger | None = None,
    best_rank_score: float | None = None,
) -> tuple[bool, EscalationTrigger | None]:
    if trigger_override is not None:
        return True, trigger_override
    if chunk_count == 0:
        return True, EscalationTrigger.no_documents
    # Use the stronger of vector similarity and hybrid rank score.
    # A high rank score (driven by BM25) indicates relevant content even when
    # the vector similarity is below the threshold — prevents false escalation
    # on lexically-matched Russian-language queries.
    candidates = [s for s in (best_similarity_score, best_rank_score) if s is not None]
    effective_score = max(candidates) if candidates else None
    if effective_score is None or effective_score < ESCALATION_THRESHOLD:
        return True, EscalationTrigger.low_similarity
    return False, None


async def detect_human_request(
    message: str,
    api_key: str,
    tenant_id: UUID | str | None = None,
    *,
    langfuse_observation: Any | None = None,
) -> HumanRequestResult:
    """Classify whether the user wants a human and whether this message has
    forwardable content.

    Returns a :class:`HumanRequestResult`. ``human_request`` means the user
    wants to be handed off to a person this turn; ``message_has_request_content``
    means this message states a concrete problem/question support could act on.
    The result is truthy iff ``human_request`` is True, preserving the legacy
    bool contract at call sites.

    Uses LLM classification so it works across all languages. The call is
    bounded by ``asyncio.wait_for`` — on timeout the underlying HTTP request
    is cancelled (the old thread-pool version could only abandon the thread).
    Falls back on timeout or error to avoid false-positive escalations.

    `tenant_id` partitions the in-memory result cache so tenants never read
    each other's classifications.
    """
    # An empty message (widget-open bootstrap turn) has nothing to classify —
    # answer deterministically instead of spending an LLM call on it.
    if not message or not message.strip():
        return HumanRequestResult(human_request=False, message_has_request_content=False)

    cache_key = hashlib.sha256(
        f"{tenant_id}:{message}".encode()
    ).hexdigest()
    cached = _human_request_cache.get(cache_key)
    if cached is not None:
        return cached

    system_prompt = (
        "Classify the user's latest message on two independent axes and "
        "return both.\n"
        "\n"
        "1. human_request — is the user *currently* asking to be connected to "
        "a human agent / operator / live support person, RIGHT NOW?\n"
        "   true: the intent is to hand the conversation off to a person this "
        "turn. Examples (illustrative): \"I want to talk to a human\", "
        "\"connect me to support\", \"I need to speak with a person\", "
        "\"can someone help me?\", \"are you there, support?\", "
        "\"please escalate this\".\n"
        "   false: the user is asking an *informational* question ABOUT "
        "support / contact options that the bot should answer from the "
        "documentation. Examples: \"how do I contact support?\", "
        "\"what's your support email?\", \"do you have a support team?\". "
        "These are knowledge questions — the user wants to know HOW to reach "
        "support, not be handed off this turn.\n"
        "\n"
        "2. message_has_request_content — does THIS message state a concrete "
        "problem, question, or request that a support agent could actually act "
        "on?\n"
        "   true: there is substance to forward. Examples: \"my payment "
        "failed\", \"the export button returns a 500\", \"I can't reset my "
        "password\", \"connect me to a human, my invoice is wrong\".\n"
        "   false: the message is only a greeting, an availability ping, or a "
        "bare request for a human with NO problem stated yet. Examples: "
        "\"hello\", \"are you there?\", \"is support online?\", "
        "\"can someone help me?\", \"I want to talk to a human\". There is "
        "nothing concrete to forward.\n"
        "\n"
        "The two axes are independent: a bare \"can someone help me?\" is "
        "human_request=true, message_has_request_content=false; a plain "
        "product question with no handoff ask is human_request=false, "
        "message_has_request_content=true.\n"
        "\n"
        "Look at intent, not exact wording. The same rules apply in any "
        "language; treat the user's phrasing as a hint, not a template.\n"
        "\n"
        'Answer ONLY with JSON: {"human_request": true/false, '
        '"message_has_request_content": true/false}'
    )

    async def _call_llm() -> HumanRequestResult:
        client = get_async_openai_client(api_key)
        response = await async_call_openai_with_retry(
            "detect_human_request",
            lambda: client.chat.completions.create(
                model=settings.human_request_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                temperature=0,
                max_completion_tokens=30,
                response_format={"type": "json_object"},
            ),
            langfuse_observation=langfuse_observation,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return HumanRequestResult(
            human_request=bool(parsed.get("human_request", False)),
            message_has_request_content=bool(
                parsed.get("message_has_request_content", False)
            ),
        )

    try:
        result = await asyncio.wait_for(_call_llm(), timeout=_HUMAN_REQUEST_TIMEOUT)
    except Exception:
        # Fail safe toward answering, not greeting. ``message_has_request_content``
        # gates whether the turn is treated as a bare social greeting (routed to
        # GreetingHandler) versus a real question (routed to RAG). On classifier
        # failure we must NOT discard a real user question with a canned
        # greeting, so default to True (the message has content worth answering).
        # ``human_request`` stays False so a failure never auto-escalates.
        return HumanRequestResult(human_request=False, message_has_request_content=True)

    _human_request_cache.set(cache_key, result)
    return result


_support_contact_cache = _LockedTTLCache(
    name="support_contact", ttl=_HUMAN_REQUEST_CACHE_TTL, maxsize=_HUMAN_REQUEST_CACHE_MAX
)


async def detect_support_contact_question(
    message: str,
    api_key: str,
    tenant_id: UUID | str | None = None,
    *,
    langfuse_observation: Any | None = None,
) -> bool:
    """Return True if the user is asking *how* to reach support / contact the team.

    Distinct from :func:`detect_human_request` (which means "hand me off to a
    human right now"). This catches informational questions like "how do I
    contact support?", "what's your support email?", "where can I get help?".
    When the knowledge base has no contact page, the bot is itself the support
    channel — so on a retrieval miss we answer about that capability instead of
    the generic "I couldn't find an answer" lead-in.

    LLM-classified so it works across all languages. Fails safe to False on
    timeout/error to keep the standard no-answer copy.
    """
    # An empty message (widget-open bootstrap turn) cannot be a contact question.
    if not message or not message.strip():
        return False

    cache_key = hashlib.sha256(f"{tenant_id}:{message}".encode()).hexdigest()
    cached = _support_contact_cache.get(cache_key)
    if cached is not None:
        return cached

    system_prompt = (
        "Decide whether the user is asking HOW to contact / reach the support "
        "team or where to get help — an informational question about support "
        "and contact options.\n"
        "\n"
        "Return true for questions like: \"how do I contact support?\", "
        "\"how can I write to support?\", \"what's your support email?\", "
        "\"where can I get help?\", \"is there a way to reach a person?\", "
        "\"do you have a support team?\".\n"
        "\n"
        "Return false for ordinary product/help questions that merely happen "
        "to need support's answer, and for an explicit \"connect me to a human "
        "right now\" hand-off request (that is handled separately).\n"
        "\n"
        "Look at intent, not exact wording. The same rule applies in any "
        "language; treat the user's phrasing as a hint, not a template.\n"
        "\n"
        'Answer ONLY with JSON: {"support_contact": true/false}'
    )

    async def _call_llm() -> bool:
        client = get_async_openai_client(api_key)
        response = await async_call_openai_with_retry(
            "detect_support_contact_question",
            lambda: client.chat.completions.create(
                model=settings.human_request_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                temperature=0,
                max_completion_tokens=20,
                response_format={"type": "json_object"},
            ),
            langfuse_observation=langfuse_observation,
        )
        raw = response.choices[0].message.content or "{}"
        return bool(json.loads(raw).get("support_contact", False))

    try:
        result = await asyncio.wait_for(_call_llm(), timeout=_HUMAN_REQUEST_TIMEOUT)
    except Exception:
        return False

    _support_contact_cache.set(cache_key, result)
    return result


def compute_priority(
    trigger: EscalationTrigger,
    plan_tier: str | None,
    user_context: dict | None,
) -> EscalationPriority:
    tier = (plan_tier or (user_context or {}).get("plan_tier") or "").lower()
    enterprise = tier in ("enterprise", "pro")

    # user_complaint (guard-detected frustration about support silence) ranks
    # with an explicit human request: the user is already waiting on a reply.
    if (
        trigger in (EscalationTrigger.user_request, EscalationTrigger.user_complaint)
        and enterprise
    ):
        return EscalationPriority.critical
    if trigger in (EscalationTrigger.user_request, EscalationTrigger.user_complaint):
        return EscalationPriority.high
    if trigger in (
        EscalationTrigger.low_similarity,
        EscalationTrigger.no_documents,
        EscalationTrigger.llm_self_offer,
    ) and enterprise:
        return EscalationPriority.high
    if trigger == EscalationTrigger.answer_rejected:
        return EscalationPriority.medium
    return EscalationPriority.medium


_TICKET_NUM_RE = re.compile(r"^ESC-(\d+)$", re.IGNORECASE)


def generate_ticket_number(tenant_id: uuid.UUID, db: Session) -> str:
    """
    Generate next sequential ticket number for tenant.

    Uses MAX(ticket_number) + 1. SELECT FOR UPDATE SKIP LOCKED is an advisory
    lock on PostgreSQL; SQLite (used in tests) ignores it gracefully.
    Duplicate prevention is the UniqueConstraint; retry logic lives in
    create_escalation_ticket().
    """
    rows = (
        db.query(EscalationTicket.ticket_number)
        .filter(EscalationTicket.tenant_id == tenant_id)
        .with_for_update(skip_locked=True)
        .all()
    )
    max_n = 0
    for (num,) in rows:
        if isinstance(num, str):
            m = _TICKET_NUM_RE.match(num)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"ESC-{max_n + 1:04d}"


def _conversation_summary_from_chat(chat_id: uuid.UUID, db: Session, max_turns: int = 5) -> str | None:
    msgs = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc())
        .limit(max_turns * 2)
        .all()
    )
    if not msgs:
        return None
    msgs = list(reversed(msgs))
    lines: list[str] = []
    for m in msgs:
        role = "user" if m.role == MessageRole.user else "assistant"
        lines.append(f"{role}: {(m.content or '')[:500]}")
    return "\n".join(lines)


def _full_transcript_from_chat(
    chat_id: uuid.UUID,
    db: Session,
    *,
    max_turns: int = 10,
    extra_user_turn: tuple[str, datetime] | None = None,
    content_fn: Callable[[Message], str] = _safe_message_content,
) -> list[tuple[str, str, datetime | None]] | None:
    """Last ``max_turns`` user/assistant pairs as ``(role, content, created_at)``.

    ``extra_user_turn`` lets callers append a user message that is not yet
    persisted (the current turn — escalation notifications fire *before*
    ``_persist_turn`` runs, so without this the transcript misses the very
    message that triggered the escalation). Skipped if the last DB row is
    already a user turn with the same content (defensive against double-add
    when persistence ordering changes later).

    ``content_fn`` selects how each stored message is rendered — the default
    fully redacts it; the support email passes ``_email_message_content`` to
    keep EMAIL/IP visible. ``extra_user_turn`` text is passed through verbatim,
    so callers must redact it before handing it in.
    """
    msgs = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc())
        .limit(max_turns * 2)
        .all()
    )
    out: list[tuple[str, str, datetime | None]] = []
    if msgs:
        for m in reversed(msgs):
            role = "user" if m.role == MessageRole.user else "assistant"
            out.append((role, content_fn(m), m.created_at))
    if extra_user_turn is not None:
        text, when = extra_user_turn
        text = text.strip()
        if text and not (out and out[-1][0] == "user" and out[-1][1].strip() == text):
            out.append(("user", text, when))
    return out or None


_KYC_IDENTITY_KEYS = {"email", "name", "plan_tier", "user_id", "audience_tag", "locale", "browser_locale"}

# Gap between a turn's "▸ USER · HH:MM" label and the message text that follows
# it on the same line. Continuation lines of a multi-line message are indented
# to line up under that turn's text.
_TRANSCRIPT_LABEL_GAP = " " * 3


def _format_transcript_turn(
    role: str,
    content: str,
    when: datetime | None,
) -> list[str]:
    """Render one transcript turn as a label + message text on the same line.

    User turns are marked with ``▸`` so a support agent can scan straight to
    the customer's messages — the thing they actually need to reply to;
    assistant turns use a quieter ``·``. Multi-line messages keep their first
    line next to the label and indent the rest to line up under it. The caller
    is responsible for the blank line that separates consecutive turns and for
    date dividers.
    """
    marker = "▸" if role == "user" else "·"
    label = f"  {marker} {role.upper()}"
    when_utc = _to_utc(when)
    if when_utc is not None:
        label += f" · {when_utc.strftime('%H:%M')}"
    label += _TRANSCRIPT_LABEL_GAP
    body_lines = content.splitlines() or [content]
    indent = " " * len(label)
    out = [f"{label}{body_lines[0]}"]
    for body_line in body_lines[1:]:
        out.append(f"{indent}{body_line}" if body_line else "")
    return out


def _build_escalation_email_body(
    tenant: Tenant,
    ticket: EscalationTicket,
    db: Session,
    *,
    latest_user_text: str | None = None,
    latest_user_at: datetime | None = None,
) -> str:
    """Compose the user-safe escalation email body.

    Critical constraint: the support agent replies via plain Reply (we set
    ``Reply-To`` to the end-user's address). The user's mail client will
    quote this body back to them. So **everything in the body must be safe
    to be shown to the end user**. Internal metadata (priority, trigger,
    chat_id, match scores) lives in custom SMTP headers — see
    :func:`_build_escalation_email_headers`. Mail clients quote bodies but
    do not quote headers.

    PII policy: the question and transcript are rebuilt from the encrypted
    originals with EMAIL and IP left visible — support needs the address and
    IP the user reported to act, and both are the user's own data being quoted
    back to them. All other PII (phones, cards, passwords, API keys, identity
    documents) stays masked. Stored rows and analytics keep the fully-redacted
    copies. See :func:`_support_email_text`.

    Layout (user-safe only):
      - One-line intro
      - FROM (user's own email + name — they already know these)
      - THEIR QUESTION
      - USER'S NOTE (if present — user-provided)
      - CONVERSATION with HH:MM UTC timestamps
    """
    sep = "─" * 56
    lines: list[str] = [
        "Hello,",
        "",
        f"A user on your bot ({tenant.name}) is asking for a human reply.",
        "Reply directly to this email — your response will reach the user.",
        "",
    ]

    chat: Chat | None = ticket.chat if ticket.chat_id else None
    user_ctx: dict[str, Any] = {}
    if chat and isinstance(chat.user_context, dict):
        user_ctx = chat.user_context

    contact_email = ticket.user_email or user_ctx.get("email")
    contact_name = ticket.user_name or user_ctx.get("name")

    lines.append(sep)
    lines.append("FROM")
    if contact_email and contact_name:
        lines.append(f"  {contact_email}  ({contact_name})")
    elif contact_email:
        lines.append(f"  {contact_email}")
    elif contact_name:
        lines.append(f"  {contact_name}  (contact email not provided)")
    else:
        lines.append("  (contact details not provided)")
    lines.append("")

    lines.append(sep)
    lines.append("THEIR QUESTION")
    question_text = _email_ticket_question(ticket).strip()
    if question_text:
        for q_line in question_text.splitlines() or [question_text]:
            lines.append(f"  {q_line}")
    else:
        lines.append("  (empty)")
    lines.append("")

    if ticket.user_note:
        lines.append(sep)
        lines.append("USER'S NOTE")
        for n_line in ticket.user_note.splitlines() or [ticket.user_note]:
            lines.append(f"  {n_line}")
        lines.append("")

    extra_turn: tuple[str, datetime] | None = None
    if latest_user_text and latest_user_text.strip():
        when = latest_user_at or datetime.now(UTC)
        # ``latest_user_text`` is the raw current turn; redact it here so the
        # extra transcript row follows the same EMAIL/IP-visible policy as the
        # persisted rows rendered by ``_email_message_content``.
        extra_turn = (_support_email_text(latest_user_text), when)

    transcript: list[tuple[str, str, datetime | None]] | None = None
    if ticket.chat_id:
        transcript = _full_transcript_from_chat(
            ticket.chat_id,
            db,
            extra_user_turn=extra_turn,
            content_fn=_email_message_content,
        )
    if transcript:
        lines.append(sep)
        lines.append("CONVERSATION (UTC)")
        lines.append("")
        last_date: date | None = None
        for role, content, when in transcript:
            when_utc = _to_utc(when)
            if when_utc is not None:
                cur_date = when_utc.date()
                if last_date is not None and cur_date != last_date:
                    lines.append(f"  ── {cur_date.isoformat()} ──")
                last_date = cur_date
            lines.extend(_format_transcript_turn(role, content, when))
            lines.append("")
    elif ticket.conversation_summary:
        lines.append(sep)
        lines.append("CONVERSATION")
        # The stored summary keeps the original wording of both roles, so it is
        # fully redacted here rather than given the EMAIL/IP-visible treatment
        # reserved for user-authored turns.
        for raw_line in redact_for_egress(ticket.conversation_summary).splitlines():
            lines.append(f"  {raw_line}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _to_utc(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _build_escalation_email_headers(
    ticket: EscalationTicket,
    *,
    chat: Chat | None = None,
) -> dict[str, str]:
    """Internal ticket metadata as ``X-Chat9-*`` headers.

    Mail clients do not quote headers when the recipient hits Reply, so this is
    where priority/trigger/chat_id/match-score live — they must reach the
    support inbox but must NOT leak back to the end user via a reply-thread.
    """
    user_ctx: dict[str, Any] = {}
    if chat is None and ticket.chat_id and ticket.chat is not None:
        chat = ticket.chat
    if chat is not None and isinstance(chat.user_context, dict):
        user_ctx = chat.user_context

    headers: dict[str, str] = {
        "X-Chat9-Ticket-Number": ticket.ticket_number,
        "X-Chat9-Priority": ticket.priority.value,
        "X-Chat9-Trigger": ticket.trigger.value,
        "X-Chat9-Why-Escalated": ticket.trigger.value,
    }
    if ticket.chat_id:
        headers["X-Chat9-Chat-Id"] = str(ticket.chat_id)
    if ticket.session_id:
        headers["X-Chat9-Session-Id"] = str(ticket.session_id)
    plan = ticket.plan_tier or user_ctx.get("plan_tier")
    if plan:
        headers["X-Chat9-Plan"] = str(plan)
    user_id = ticket.user_id or user_ctx.get("user_id")
    if user_id:
        headers["X-Chat9-User-Id"] = str(user_id)
    locale = user_ctx.get("locale")
    if locale:
        headers["X-Chat9-Locale"] = str(locale)
    browser_locale = user_ctx.get("browser_locale")
    if browser_locale and browser_locale != locale:
        headers["X-Chat9-Browser-Locale"] = str(browser_locale)
    audience = user_ctx.get("audience_tag")
    if audience:
        headers["X-Chat9-Audience"] = str(audience)
    if ticket.best_similarity_score is not None:
        headers["X-Chat9-Match-Score"] = f"{ticket.best_similarity_score:.4f}"
    kyc_extras: dict[str, Any] = {}
    for key, value in user_ctx.items():
        if key in _KYC_IDENTITY_KEYS:
            continue
        if value is None or value == "":
            continue
        kyc_extras[key] = value
    if kyc_extras:
        try:
            headers["X-Chat9-KYC"] = json.dumps(kyc_extras, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            pass
    return headers


# Window for collapsing rapid user keystrokes into a single follow-up email
# to support. Keeps the support inbox readable when a user fires off three
# short messages while typing out their context.
_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS = 60

# Statuses that mean "this request is still live work". ``in_progress`` joined
# ``open`` when the operator handoff started claiming tickets: a request a
# human is holding is not finished, and every rule that used to ask "is this
# ticket open?" means "is it still live work?" — reuse instead of minting a
# second ESC number, thread follow-up turns onto it, age it out when the
# conversation is over. Only ``resolved`` and ``auto_closed`` are terminal.
ACTIVE_TICKET_STATUSES = (EscalationStatus.open, EscalationStatus.in_progress)


def _support_inbox_recipient(tenant: Tenant, db: Session) -> str | None:
    """Where this tenant's support notifications go, or ``None``.

    The configured L2 address if there is one, else the workspace owner.
    """
    user = db.query(User).filter(User.tenant_id == tenant.id, User.role == "owner").first()
    support_config = public_support_config_dict(
        tenant.settings if isinstance(tenant.settings, dict) else None
    )
    return support_config["l2_email"] or (user.email if user and user.email else None)


def _send_email_off_loop(*args: Any, **kwargs: Any) -> str | None:
    """Send the notification without blocking the event loop.

    ``send_email`` is a synchronous Brevo HTTP call (up to 10s). The escalation
    notify helpers run in two contexts: inside the chat pipeline / async
    escalate routes they execute in a ``run_sync`` greenlet ON the event-loop
    thread, where a direct call would stall every in-flight turn for the
    duration of the send — bridge it to a worker thread via ``await_only`` +
    ``asyncio.to_thread``. In plain sync contexts (tests, sync tooling) there
    is no greenlet, so fall back to the direct call.
    """
    try:
        return await_only(asyncio.to_thread(send_email, *args, **kwargs))
    except MissingGreenlet:
        return send_email(*args, **kwargs)


def _report_escalation_email_failure(
    tenant: Tenant | None,
    ticket: EscalationTicket,
    *,
    reason: str,
    stage: str,
    error: BaseException | None = None,
) -> None:
    """Raise an internal alert when an escalation notification fails to send.

    Escalation emails are best-effort: a failed send is otherwise only visible
    in a ``logger.warning`` line — never reaching Sentry (no exception escapes
    the notify path) or product metrics. That blind spot let a broken Brevo
    integration silently drop every support notification while chats and
    tickets kept working normally. This surfaces the failure to our internal
    monitoring only (Sentry alert + PostHog metric); it never reaches the
    tenant dashboard or the end user.

    ``reason`` is the failure mode: ``"brevo_refused"`` when ``send_email``
    returned ``None`` (Brevo HTTP 4xx/5xx or an internal error), or
    ``"send_exception"`` when the call itself raised. ``stage`` is
    ``"initial"`` (new-ticket notify) or ``"followup"`` (threaded update).

    ``error`` (when the send raised) contributes only its exception *type name*
    for triage — never the message/args, which can carry the recipient address
    or other PII.

    Never raises — observability must not break the notify path.
    """
    tenant_id = str(tenant.id) if tenant is not None else None
    # Type name only — an exception message can embed the recipient email
    # (PII); the class name is enough to tell a network error from an auth one.
    error_type = type(error).__name__ if error is not None else None
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            # error_kind + tenant power the 60s Sentry dedup (see
            # observability/sentry._before_send), so a Brevo outage raises one
            # alert per tenant per minute instead of one per dropped ticket.
            scope.set_tag("error_kind", "escalation_email_send_failed")
            scope.set_tag("escalation_email_reason", reason)
            if tenant_id is not None:
                scope.set_context(
                    "tenant",
                    {"tenant_id": tenant_id, "tenant_name": getattr(tenant, "name", None)},
                )
            scope.set_context(
                "escalation_email",
                {
                    "ticket_number": ticket.ticket_number,
                    "reason": reason,
                    "stage": stage,
                    "error_type": error_type,
                },
            )
            sentry_sdk.capture_message(
                f"Escalation notification email failed to send ({reason})",
                level="error",
                scope=scope,
            )
    except Exception:
        # Sentry not installed / not initialized, or capture failed — the
        # PostHog metric below still fires. Never propagate.
        pass

    # PostHog captures every occurrence (no dedup) so the failure rate is
    # measurable even during a sustained outage. ``capture_event`` is itself a
    # no-op when metrics are disabled and swallows its own errors.
    capture_event(
        "escalation.email_send_failed",
        distinct_id=tenant_id or "system",
        tenant_id=tenant_id,
        properties={
            "reason": reason,
            "stage": stage,
            "ticket_number": ticket.ticket_number,
            "error_type": error_type,
        },
        groups={"tenant": tenant_id} if tenant_id else None,
    )


def _notify_tenant_new_ticket(
    tenant: Tenant,
    ticket: EscalationTicket,
    db: Session,
    *,
    latest_user_text: str | None = None,
    latest_user_at: datetime | None = None,
) -> bool:
    """Send the escalation notification to the tenant's support inbox.

    Skipped when the ticket has no usable end-user email: support cannot reply
    without a contact, so a notification at this point would be a no-op pinging
    a no-reply mailbox. The ticket itself still exists in the dashboard as a
    signal (gap analysis, queue review), and the notification is re-attempted
    later via :func:`apply_collected_contact_email` once the user provides a
    valid email.

    ``latest_user_text`` is the current user turn — escalation notifications
    fire *before* persistence runs, so the DB transcript misses the very
    message that triggered the escalation unless we thread it through.
    """
    if not _is_valid_email(ticket.user_email):
        if ticket.user_email:
            logger.info(
                "escalation_email_skipped_invalid_user_email tenant_id=%s ticket=%s",
                tenant.id,
                ticket.ticket_number,
            )
        else:
            logger.info(
                "escalation_email_deferred_no_user_email tenant_id=%s ticket=%s",
                tenant.id,
                ticket.ticket_number,
            )
        return False

    user = db.query(User).filter(User.tenant_id == tenant.id, User.role == "owner").first()
    support_config = public_support_config_dict(tenant.settings if isinstance(tenant.settings, dict) else None)
    recipient = support_config["l2_email"] or (user.email if user and user.email else None)
    if not recipient:
        logger.warning("No escalation notification email configured for tenant_id=%s", tenant.id)
        return False

    body = _build_escalation_email_body(
        tenant,
        ticket,
        db,
        latest_user_text=latest_user_text,
        latest_user_at=latest_user_at,
    )
    headers = _build_escalation_email_headers(ticket)
    question_preview = _safe_ticket_question(ticket).replace("\n", " ").strip()[:60]
    # Subject deliberately omits priority tier (`HIGH`/`CRITICAL`) — the user
    # will see the subject prefixed with `Re:` if support replies and we don't
    # want to leak our internal urgency classification back to them. The ticket
    # number is fine: it's a tenant-facing identifier the user may already
    # know from the bot's acknowledgement message.
    subject = f"[{ticket.ticket_number}] {question_preview}".rstrip(" —-")
    try:
        send_result = _send_email_off_loop(
            recipient,
            subject,
            body,
            reply_to=ticket.user_email,
            extra_headers=headers,
        )
    except Exception as e:
        logger.warning("Escalation email failed: %s", e)
        _report_escalation_email_failure(
            tenant, ticket, reason="send_exception", stage="initial", error=e
        )
        return False

    if send_result is None:
        # Brevo refused the send (HTTP 4xx/5xx) or the call raised internally.
        # Do NOT advance any "already notified" markers — without this guard a
        # failed initial notify would set ``last_notified_*`` while leaving
        # ``notification_message_id`` empty, which makes every subsequent call
        # to ``_notify_tenant_ticket_update`` skip (anchor missing) and
        # permanently suppresses notifications for this ticket.
        _report_escalation_email_failure(
            tenant, ticket, reason="brevo_refused", stage="initial"
        )
        return False

    # Mark the high-water line for follow-up update emails. Empty-string
    # ``send_result`` means the send succeeded but no Message-ID is available
    # (dev-mode, or Brevo response without ``messageId``) — we still advance
    # the marker so the initial-notify turns are not re-sent as delta later;
    # the missing anchor will simply cause ``_notify_tenant_ticket_update`` to
    # skip threaded updates, which is the right degraded behaviour.
    # Use the project-wide ``_utcnow()`` (naive UTC). All datetime columns on
    # this row — including ``last_notified_at`` and ``updated_at`` — are
    # ``TIMESTAMP WITHOUT TIME ZONE`` (no ``timezone=True``); psycopg2 silently
    # drops ``tzinfo`` for aware values but asyncpg raises ``DataError``,
    # rolling back the transaction and surfacing as "Internal error" in the
    # widget. See ``backend/models/base._utcnow`` for the rationale.
    now = _utcnow()
    if isinstance(send_result, str) and send_result:
        ticket.notification_message_id = send_result
    ticket.last_notified_at = now
    ticket.last_notified_message_id = _current_high_user_message_id(
        ticket.chat_id, db, fallback_at=now
    )
    db.add(ticket)
    db.flush()
    return True

def _current_high_user_message_id(
    chat_id: uuid.UUID | None,
    db: Session,
    *,
    fallback_at: datetime,
) -> uuid.UUID | None:
    """ID of the most recent persisted user message for ``chat_id``.

    Used to seed ``ticket.last_notified_message_id`` after the initial notify
    so the first update email starts from turns that come *after* this one.
    Returns ``None`` if the triggering user turn has not been persisted yet
    (escalation notifications fire before ``_persist_turn`` runs) — the next
    update will compare against ``last_notified_at`` instead.
    """
    if chat_id is None:
        return None
    row = (
        db.query(Message.id)
        .filter(Message.chat_id == chat_id, Message.role == MessageRole.user)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .first()
    )
    return row[0] if row else None


def advance_notification_marker_to_current(
    ticket: EscalationTicket,
    db: Session,
) -> None:
    """Advance ``last_notified_message_id`` to the chat's newest persisted user
    message and ``last_notified_at`` to now.

    Used after the initial-notify flow when the very turn that triggered the
    notify is persisted *afterwards* (the initial email body already included
    it via ``latest_user_text``). Without this fixup, the next call to
    :func:`_notify_tenant_ticket_update` would treat the email-capture turn as
    unsent delta and double-send it under the threaded reply.
    """
    if ticket.chat_id is None:
        return
    high_id = _current_high_user_message_id(ticket.chat_id, db, fallback_at=_utcnow())
    if high_id is None:
        return
    ticket.last_notified_message_id = high_id
    # Naive UTC — column is ``DateTime`` (no ``timezone=True``); see the note
    # in ``_notify_tenant_new_ticket`` above.
    ticket.last_notified_at = _utcnow()
    db.add(ticket)
    db.flush()


def _format_update_email_body(
    turns: list[tuple[str, datetime | None]],
) -> str:
    """User-safe delta body for a follow-up notification.

    Same constraint as the initial notify: support replies via plain Reply
    and their mail client quotes the body back to the end user, so only
    safe content goes here. Internal metadata stays in headers. Layout is
    intentionally minimal — just the new user turns since last notify. Turns
    are rendered with the same EMAIL/IP-visible PII policy as the initial
    notify (see :func:`_build_escalation_email_body`); the caller redacts each
    turn via ``_email_message_content`` / ``_support_email_text``.
    """
    sep = "─" * 56
    lines: list[str] = [
        "Hello,",
        "",
        "The user added more context to their request:",
        "",
        sep,
        "NEW MESSAGES (UTC)",
        "",
    ]
    last_date: date | None = None
    for content, when in turns:
        when_utc = _to_utc(when)
        if when_utc is not None:
            cur_date = when_utc.date()
            if last_date is not None and cur_date != last_date:
                lines.append(f"  ── {cur_date.isoformat()} ──")
            last_date = cur_date
        lines.extend(_format_transcript_turn("user", content, when))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _notify_tenant_ticket_update(
    ticket: EscalationTicket,
    db: Session,
    *,
    extra_user_turn: tuple[str, datetime] | None = None,
    force: bool = False,
) -> bool:
    """Forward new user turns on an active ticket to the support inbox.

    Threaded as a reply to the original notify email so support's mail client
    groups it under the same conversation. Body is delta-only — just the user
    turns since ``last_notified_message_id`` (or since ``last_notified_at`` if
    the id was lost) — in the same user-safe format as the initial notify.

    Skipped silently when any of the following hold:
      * ticket is not open;
      * the chat has ended;
      * no contact email or recipient configured;
      * the initial notify never went out (no anchor to thread under);
      * we sent another update less than ``_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS``
        ago — the next eligible user turn after the window will pick up the
        skipped delta because ``last_notified_message_id`` has not moved;
      * no new user turns to report.

    ``extra_user_turn`` lets the caller include the current turn that has not
    yet been persisted (escalation handlers run before ``_persist_turn``).
    """
    if ticket.status not in ACTIVE_TICKET_STATUSES:
        return False
    if not _is_valid_email(ticket.user_email):
        return False
    if ticket.notification_message_id is None:
        # Without the Message-ID anchor we cannot thread the update under the
        # initial notify; sending a standalone email here would split the
        # conversation in the support inbox. The initial notify also captures
        # the full transcript so support already has this context.
        return False

    chat: Chat | None = ticket.chat
    if chat is not None and chat.ended_at is not None:
        return False

    tenant = ticket.tenant
    if tenant is None:
        return False
    recipient = _support_inbox_recipient(tenant, db)
    if not recipient:
        return False

    now = datetime.now(UTC)
    last_at = _to_utc(ticket.last_notified_at)
    if (
        not force
        and last_at is not None
        and (now - last_at).total_seconds() < _FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS
    ):
        return False

    # Build the delta: user turns persisted after the high-water mark.
    boundary_at: datetime | None = None
    if ticket.last_notified_message_id is not None:
        boundary_row = (
            db.query(Message.created_at)
            .filter(Message.id == ticket.last_notified_message_id)
            .first()
        )
        if boundary_row and boundary_row[0] is not None:
            boundary_at = _to_utc(boundary_row[0])
    if boundary_at is None:
        boundary_at = last_at

    new_msgs: list[Message] = []
    if ticket.chat_id is not None:
        q = db.query(Message).filter(
            Message.chat_id == ticket.chat_id,
            Message.role == MessageRole.user,
        )
        if boundary_at is not None:
            q = q.filter(Message.created_at > boundary_at)
        new_msgs = q.order_by(Message.created_at.asc(), Message.id.asc()).all()
        # Filter out the boundary row itself in case the boundary timestamp
        # is identical (SQLite has second-level precision in tests).
        if ticket.last_notified_message_id is not None:
            new_msgs = [m for m in new_msgs if m.id != ticket.last_notified_message_id]

    turns: list[tuple[str, datetime | None]] = [
        (_email_message_content(m), m.created_at) for m in new_msgs
    ]
    if extra_user_turn is not None:
        text, when = extra_user_turn
        # Same EMAIL/IP-visible policy as the persisted delta rows above;
        # ``extra_user_turn`` carries the raw current turn.
        text = _support_email_text(text).strip()
        if text and not (turns and turns[-1][0].strip() == text):
            turns.append((text, when))

    turns = [(t, w) for t, w in turns if t and t.strip()]
    if not turns:
        return False

    body = _format_update_email_body(turns)
    headers = _build_escalation_email_headers(ticket, chat=chat)
    headers["In-Reply-To"] = ticket.notification_message_id
    headers["References"] = ticket.notification_message_id
    question_preview = _safe_ticket_question(ticket).replace("\n", " ").strip()[:60]
    subject = f"Re: [{ticket.ticket_number}] {question_preview}".rstrip(" —-")

    try:
        send_result = _send_email_off_loop(
            recipient,
            subject,
            body,
            reply_to=ticket.user_email,
            extra_headers=headers,
        )
    except Exception as e:
        logger.warning("Escalation follow-up email failed (ticket=%s): %s", ticket.ticket_number, e)
        _report_escalation_email_failure(
            tenant, ticket, reason="send_exception", stage="followup", error=e
        )
        return False

    if send_result is None:
        # Brevo refused the send. Do NOT advance the marker — the delta we
        # just tried to deliver must remain eligible for a retry on the next
        # eligible user turn.
        _report_escalation_email_failure(
            tenant, ticket, reason="brevo_refused", stage="followup"
        )
        return False

    # ``now`` above is timezone-aware (UTC) and used only for debounce
    # arithmetic. The column is ``DateTime`` (naive, no ``timezone=True``);
    # writing aware values crashes asyncpg with ``DataError`` and rolls back
    # the transaction. See ``models/base._utcnow`` for the project policy.
    ticket.last_notified_at = _utcnow()
    if new_msgs:
        ticket.last_notified_message_id = new_msgs[-1].id
    db.add(ticket)
    db.flush()
    return True

def create_escalation_ticket(
    tenant_id: uuid.UUID,
    primary_question: str,
    trigger: EscalationTrigger,
    db: Session,
    *,
    chat_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    best_similarity_score: float | None = None,
    retrieved_chunks: list[dict[str, Any]] | None = None,
    conversation_turns: list[str] | None = None,
    user_context: dict | None = None,
    user_note: str | None = None,
    optional_entity_types: set[str] | None = None,
    latest_user_text: str | None = None,
) -> EscalationTicket:
    from sqlalchemy.exc import IntegrityError

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise ValueError("tenant not found")
    if optional_entity_types is None:
        optional_entity_types = _tenant_optional_entity_types(tenant)

    summary: str | None = None
    if chat_id:
        summary = _conversation_summary_from_chat(chat_id, db)
    if conversation_turns:
        summary = "\n".join(conversation_turns[-5:])

    uid = (user_context or {}).get("user_id")
    email = (user_context or {}).get("email")
    name = (user_context or {}).get("name")
    plan = (user_context or {}).get("plan_tier")

    priority = compute_priority(trigger, plan, user_context)
    # Audit-only pass: records which entity types get masked when this question
    # is forwarded (support email, OpenAI). The stored column keeps the
    # original wording.
    redaction = redact(primary_question, optional_entity_types=optional_entity_types)

    ticket: EscalationTicket | None = None
    for attempt in range(3):
        ticket_number = generate_ticket_number(tenant_id, db)
        ticket = EscalationTicket(
            tenant_id=tenant_id,
            ticket_number=ticket_number,
            primary_question=primary_question[:8000],
            conversation_summary=summary,
            trigger=trigger,
            best_similarity_score=best_similarity_score,
            retrieved_chunks_preview=retrieved_chunks,
            user_id=str(uid) if uid else None,
            user_email=str(email) if email else None,
            user_name=str(name) if name else None,
            plan_tier=str(plan) if plan else None,
            user_note=user_note,
            priority=priority,
            status=EscalationStatus.open,
            chat_id=chat_id,
            session_id=session_id,
        )
        db.add(ticket)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
            continue

    assert ticket is not None
    db.refresh(ticket)
    if redaction.was_redacted:
        for entity in redaction.entities_found:
            db.add(
                PiiEvent(
                    tenant_id=tenant_id,
                    chat_id=chat_id,
                    message_id=None,
                    direction=PiiEventDirection.escalation_ticket,
                    entity_type=entity.type,
                    count=entity.count,
                )
            )
        db.commit()
        db.refresh(ticket)

    try:
        _notify_tenant_new_ticket(tenant, ticket, db, latest_user_text=latest_user_text)
    except Exception as e:
        logger.warning("notify tenant owner failed (ticket still created): %s", e)

    return ticket


_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)


def _is_valid_email(value: str | None) -> bool:
    """Strict full-match check used to gate values handed to the email provider.

    ``ticket.user_email`` may originate from widget-supplied user_context and is
    not guaranteed to be syntactically valid. Passing a malformed value as
    Reply-To causes Brevo to reject the entire send, suppressing the support
    notification — so we drop the header silently when validation fails.
    """
    if not value:
        return False
    candidate = value.strip()
    if not candidate or len(candidate) > 320:
        return False
    return _EMAIL_RE.fullmatch(candidate) is not None


def parse_contact_email(message: str) -> str | None:
    found = _EMAIL_RE.findall(message.strip())
    if len(found) != 1:
        return None
    return found[0].lower()


def apply_collected_contact_email(
    ticket_id: uuid.UUID,
    chat_id: uuid.UUID,
    email: str,
    db: Session,
    *,
    latest_user_text: str | None = None,
) -> None:
    ticket = db.query(EscalationTicket).filter(EscalationTicket.id == ticket_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not ticket or not chat:
        return
    # Notify the support inbox lazily: ticket creation skips the email when no
    # contact is known, so the first time we get a valid email is when support
    # actually has something to act on.
    notify_late = not _is_valid_email(ticket.user_email) and _is_valid_email(email)
    ticket.user_email = email
    if notify_late and ticket.status == EscalationStatus.auto_closed:
        # A chat awaiting an email blocks rotation, so it can sit idle long
        # enough for the sweeper to age its ticket out. If the user eventually
        # answers, support is about to hear about this request for the first
        # time — it must be open, or it would be emailed out while reading as
        # closed in the dashboard (and later turns could not thread onto it).
        ticket.status = EscalationStatus.open
        ticket.resolved_at = None
    ctx = dict(chat.user_context or {})
    ctx["email"] = email
    chat.user_context = ctx
    chat.escalation_awaiting_ticket_id = None
    chat.escalation_followup_pending = True
    db.add(ticket)
    db.add(chat)
    sync_user_session_identity(
        db,
        tenant_id=chat.tenant_id,
        user_context=ctx,
    )
    db.flush()
    if notify_late and ticket.tenant is not None:
        try:
            _notify_tenant_new_ticket(
                ticket.tenant,
                ticket,
                db,
                latest_user_text=latest_user_text,
            )
        except Exception as e:
            logger.warning(
                "deferred escalation email failed (ticket=%s): %s",
                ticket.ticket_number,
                e,
            )


def resolve_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID,
    resolution_text: str,
    db: Session,
) -> EscalationTicket:
    ticket = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.id == ticket_id, EscalationTicket.tenant_id == tenant_id)
        .first()
    )
    if not ticket:
        raise ValueError("ticket not found")

    ticket.status = EscalationStatus.resolved
    ticket.resolution_text = resolution_text
    # Naive UTC: ``resolved_at`` is ``DateTime`` (no ``timezone=True``); see
    # the note in ``_notify_tenant_new_ticket`` for the asyncpg rationale.
    ticket.resolved_at = _utcnow()
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


_PRIORITY_ORDER = {
    EscalationPriority.low: 0,
    EscalationPriority.medium: 1,
    EscalationPriority.high: 2,
    EscalationPriority.critical: 3,
}


def notify_support_of_repeat_escalation(
    ticket: EscalationTicket,
    db: Session,
    *,
    latest_user_text: str,
    when: datetime | None = None,
) -> bool:
    """Deliver a reused ticket's new turn to support. Returns whether it sent.

    An escalation is an *active* event and must reach support as reliably as a
    freshly created ticket does — the bot tells the user "passed to support" and
    returns a ticket number either way. The passive follow-up path is not good
    enough on its own for two reasons, both of which silently swallowed the
    handoff before this existed:

    * its ``_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS`` window exists to keep chatty
      follow-up turns from flooding the inbox, but the create path it replaces
      here has no debounce at all — and the marker advance at the end of that
      path guarantees a repeat inside the window. Bypassed via ``force``.
    * it no-ops forever when the initial notify never landed a Message-ID
      anchor (Brevo refused, or the send raised — ``create_escalation_ticket``
      swallows that). Before ticket reuse, the next repeat minted a new ticket
      and re-attempted the send, so a transient outage self-healed; now the
      reuse guard would latch onto the broken ticket. So re-attempt the initial
      notify instead, which also restores the anchor for later updates.
    """
    if ticket.notification_message_id is None:
        tenant = ticket.tenant
        if tenant is None:
            return False
        return _notify_tenant_new_ticket(
            tenant, ticket, db, latest_user_text=latest_user_text
        )
    return _notify_tenant_ticket_update(
        ticket,
        db,
        extra_user_turn=(latest_user_text, when or _utcnow()),
        force=True,
    )


def raise_ticket_priority_if_higher(
    ticket: EscalationTicket,
    trigger: EscalationTrigger,
    user_context: dict | None,
    db: Session,
) -> None:
    """Escalate a reused ticket's priority when the new request outranks it.

    Reuse keeps the original ticket's fields, which is right for the question
    itself but wrong for urgency: a ``critical`` request must not sit at
    ``medium`` because it arrived second in the same conversation. Priority
    only ever moves up here — a later low-priority turn cannot demote a ticket
    support is already treating as urgent.
    """
    plan = (user_context or {}).get("plan_tier")
    new_priority = compute_priority(trigger, plan, user_context)
    if _PRIORITY_ORDER.get(new_priority, 0) <= _PRIORITY_ORDER.get(ticket.priority, 0):
        return
    ticket.priority = new_priority
    db.add(ticket)


def mark_ticket_in_progress(db: Session, *, chat_id: uuid.UUID) -> EscalationTicket | None:
    """Move this chat's open ticket to ``in_progress``. Returns it, or ``None``.

    Called from both operator entry points — the explicit ``/take`` and the
    implicit claim inside ``ingest_from_operator`` — because both mean the
    same thing: a person has picked this request up. The escalations inbox
    then shows reality instead of leaving a request someone is already
    holding indistinguishable from one nobody has looked at.

    Only a ticket in ``open`` moves. A ``resolved`` or ``auto_closed`` ticket
    is terminal and must not be dragged back into the queue by an operator
    opening the conversation to read it; a ticket already ``in_progress`` is
    left as it is, so a colleague joining a shared thread does not restamp
    anything.

    The claim time itself is not recorded here — ``chats.operator_joined_at``
    already holds it, and one clock is better than two that can disagree.
    """
    ticket = (
        db.query(EscalationTicket)
        .filter(
            EscalationTicket.chat_id == chat_id,
            EscalationTicket.status == EscalationStatus.open,
        )
        .order_by(EscalationTicket.created_at.desc())
        .first()
    )
    if ticket is None:
        return None
    ticket.status = EscalationStatus.in_progress
    db.add(ticket)
    return ticket


def notify_support_of_abandoned_claim(ticket: EscalationTicket, db: Session) -> bool:
    """Tell support a claimed request was dropped. Returns whether it sent.

    An operator claimed the conversation and never wrote a word. Without this
    the request is *worse off* than if nobody had touched it: an unclaimed
    ticket at least sits visibly ``open`` in the inbox, whereas a claimed one
    ages out to ``auto_closed`` on the normal idle path, indistinguishable
    from a request that was answered and ended naturally.

    Threaded under the original notify's Message-ID so it lands in the same
    support conversation, and deliberately given its own body rather than
    reusing ``_format_update_email_body`` — that one opens with "The user
    added more context to their request", which here would be a plain
    falsehood. Everything else (recipient resolution, header construction,
    the off-loop Brevo send, failure reporting) is the shared machinery.

    Caller is responsible for the once-per-ticket cap (``claim_bounced_at``)
    and for putting the status back to ``open`` first.
    """
    tenant = ticket.tenant
    if tenant is None:
        return False
    if not _is_valid_email(ticket.user_email):
        return False
    if ticket.notification_message_id is None:
        # No anchor — the initial notify never landed. A standalone e-mail
        # here would split the conversation in the support inbox, and the
        # request was never announced in the first place, so re-run the
        # initial notify instead: it carries the full transcript and restores
        # the anchor for anything later.
        return _notify_tenant_new_ticket(
            tenant, ticket, db, latest_user_text=_safe_ticket_question(ticket)
        )
    recipient = _support_inbox_recipient(tenant, db)
    if not recipient:
        return False

    headers = _build_escalation_email_headers(ticket, chat=ticket.chat)
    headers["In-Reply-To"] = ticket.notification_message_id
    headers["References"] = ticket.notification_message_id
    question_preview = _safe_ticket_question(ticket).replace("\n", " ").strip()[:60]
    subject = f"Re: [{ticket.ticket_number}] {question_preview}".rstrip(" —-")
    # User-safe: support replies by hitting Reply, and their mail client
    # quotes this body back to the end user. Nothing here says anything the
    # end user should not read.
    body = "\n".join(
        [
            "Hello,",
            "",
            "This request was picked up but has not been answered, so it is "
            "back in the queue and still needs a reply.",
            "",
            f"Ticket: {ticket.ticket_number}",
            "",
            "The original request and full transcript are in the message this "
            "is a reply to.",
        ]
    )

    try:
        send_result = _send_email_off_loop(
            recipient,
            subject,
            body,
            reply_to=ticket.user_email,
            extra_headers=headers,
        )
    except Exception as e:
        logger.warning(
            "Abandoned-claim email failed (ticket=%s): %s", ticket.ticket_number, e
        )
        _report_escalation_email_failure(
            tenant, ticket, reason="send_exception", stage="claim_bounce", error=e
        )
        return False
    if send_result is None:
        _report_escalation_email_failure(
            tenant, ticket, reason="brevo_refused", stage="claim_bounce"
        )
        return False
    return True


def get_open_escalation_ticket_for_chat(
    chat_id: uuid.UUID, db: Session
) -> EscalationTicket | None:
    """Newest still-open ticket for this chat, or None.

    Guards the "one open ticket per chat" rule: a user who keeps asking for a
    human — re-phrasing, repeating, or answering a second pre-confirm offer —
    must land on the existing ticket rather than minting a fresh ESC number
    per turn. Support loses nothing by the reuse: the initial notify already
    carries the full transcript, and each later turn is threaded under it by
    :func:`_notify_tenant_ticket_update`.

    Scoped to the *active* statuses so a chat that continues after support
    closed the ticket can still raise a genuinely new one. ``in_progress``
    counts as active: an operator holding the request is the strongest reason
    of all to reuse its ticket rather than mint a second ESC number behind
    their back.
    """
    return (
        db.query(EscalationTicket)
        .filter(
            EscalationTicket.chat_id == chat_id,
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
        )
        .order_by(EscalationTicket.created_at.desc())
        .first()
    )


def get_latest_escalation_ticket_for_chat(chat_id: uuid.UUID, db: Session) -> EscalationTicket:
    ticket = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.chat_id == chat_id)
        .order_by(EscalationTicket.created_at.desc())
        .first()
    )
    if not ticket:
        logger.error("escalation_followup_pending but no ticket for chat_id=%s", chat_id)
        raise ValueError("no escalation ticket for chat")
    return ticket


def fact_from_ticket(
    ticket: EscalationTicket,
    chat: Chat | None = None,
    sla_hours: int = 24,
) -> dict[str, Any]:
    user_ctx = (chat.user_context or {}) if chat else {}
    locale = user_ctx.get("locale") or user_ctx.get("browser_locale")
    return {
        "ticket_number": ticket.ticket_number,
        "sla_hours": sla_hours,
        "user_email": ticket.user_email,
        "trigger": ticket.trigger.value,
        "priority": ticket.priority.value,
        "locale": locale,
    }


def transcript_messages_for_openai(
    chat: Chat, optional_entity_types: set[str] | None = None
) -> list[dict[str, str]]:
    """Stored transcript as OpenAI ``messages`` — redacted at this boundary.

    Rows keep the original wording, so every turn is masked here before the
    escalation LLM call sees it.
    """
    msgs: list[dict[str, str]] = []
    for m in sorted(chat.messages, key=lambda x: x.created_at or x.id):
        # Skip empty-content messages (defensive guard; bootstrap no longer persists
        # empty user messages, but old sessions may still have them in the DB).
        if not (m.content or "").strip():
            continue
        role = "user" if m.role == MessageRole.user else "assistant"
        msgs.append(
            {"role": role, "content": _safe_message_content(m, optional_entity_types)}
        )
    return msgs


def build_chat_messages_for_openai(
    chat: Chat,
    current_user_text: str,
    optional_entity_types: set[str] | None = None,
) -> list[dict[str, str]]:
    """History + the current turn for an escalation LLM call.

    ``current_user_text`` must already be redacted by the caller (handlers pass
    ``ctx.redacted_question``); the stored history is redacted here.
    """
    msgs = transcript_messages_for_openai(chat, optional_entity_types)
    msgs.append({"role": "user", "content": current_user_text})
    return msgs


def _escalation_clarify_already_asked(chat: Chat) -> bool:
    return bool((chat.user_context or {}).get(_CLARIFY_KEY))


def _set_escalation_clarify_flag(chat: Chat) -> None:
    ctx = dict(chat.user_context or {})
    ctx[_CLARIFY_KEY] = True
    chat.user_context = ctx


def _clear_escalation_clarify_flag(chat: Chat) -> None:
    ctx = dict(chat.user_context or {})
    ctx.pop(_CLARIFY_KEY, None)
    chat.user_context = ctx


async def perform_manual_escalation(
    db: AsyncSession,
    tenant: Tenant,
    session_id: uuid.UUID,
    *,
    api_key: str,
    user_note: str | None,
    trigger: EscalationTrigger,
    bot_public_id: str | None = None,
    failure_type: str | None = None,
    original_user_message: str | None = None,
) -> tuple[str, str]:
    """
    Create ticket + OpenAI handoff; persist assistant message only (no user bubble).
    Returns (message_to_user, ticket_number).

    Async entry point: the ORM work runs on the session's sync facade inside a
    ``run_sync`` greenlet, and the escalation LLM call inside it is awaited on
    the event loop via ``await_only`` — no executor thread is held for the
    OpenAI round-trip.

    For ``trigger == EscalationTrigger.llm_unavailable`` the OpenAI handoff is
    skipped entirely (the LLM is the failing dependency) and the user-facing
    message is taken from the static i18n table. ``failure_type`` is recorded
    in ``user_note``; ``original_user_message`` becomes the ticket's
    ``primary_question``.
    """
    from backend.core.db import run_sync

    return await run_sync(
        db,
        lambda s: _perform_manual_escalation_impl(
            s,
            tenant,
            session_id,
            api_key=api_key,
            user_note=user_note,
            trigger=trigger,
            bot_public_id=bot_public_id,
            failure_type=failure_type,
            original_user_message=original_user_message,
        ),
    )


def _perform_manual_escalation_impl(
    db: Session,
    tenant: Tenant,
    session_id: uuid.UUID,
    *,
    api_key: str,
    user_note: str | None,
    trigger: EscalationTrigger,
    bot_public_id: str | None = None,
    failure_type: str | None = None,
    original_user_message: str | None = None,
) -> tuple[str, str]:
    """Greenlet body of :func:`perform_manual_escalation`.

    Runs on the ``AsyncSession`` sync facade, so it may only be called from
    inside ``run_sync`` (the ``await_only`` bridge below requires the active
    greenlet context).
    """
    from backend.chat.llm_unavailable_copy import support_notified_text
    from backend.escalation.openai_escalation import complete_escalation_openai_turn
    from backend.models import Chat, EscalationPhase

    chat = (
        db.query(Chat)
        .filter(Chat.session_id == session_id, Chat.tenant_id == tenant.id)
        .order_by(Chat.created_at.desc())
        .first()
    )
    if not chat:
        raise ValueError("session not found")

    effective = dict(chat.user_context) if chat.user_context else {}
    optional_entity_types = _tenant_optional_entity_types(tenant)

    is_llm_unavailable = trigger == EscalationTrigger.llm_unavailable
    enriched_note = user_note
    if is_llm_unavailable and failure_type:
        prefix = f"[llm_failure: {failure_type}]"
        enriched_note = f"{prefix} {user_note}".strip() if user_note else prefix

    primary_question_override = (
        original_user_message if is_llm_unavailable and original_user_message else None
    )
    # One open ticket per chat — repeated presses of "Talk to support" must
    # land on the existing ticket instead of minting a fresh ESC number each
    # time. Mirrors the same rule in the chat escalation FSM.
    existing_ticket = get_open_escalation_ticket_for_chat(chat.id, db)
    reused = existing_ticket is not None
    if existing_ticket is not None:
        ticket = existing_ticket
        raise_ticket_priority_if_higher(ticket, trigger, effective, db)
        try:
            # ``enriched_note``, not ``user_note`` — it carries the
            # ``[llm_failure: ...]`` prefix support needs to read a repeat press
            # during an outage correctly.
            notify_support_of_repeat_escalation(
                ticket,
                db,
                latest_user_text=(
                    primary_question_override or enriched_note or "(manual escalation)"
                ),
            )
        except Exception as e:
            logger.warning(
                "repeat manual-escalation notify failed (ticket=%s): %s",
                ticket.ticket_number,
                e,
            )
        # Close the write transaction before the OpenAI handoff below; the
        # create branch commits inside create_escalation_ticket. See the same
        # note in chat/handlers/escalation.py::_create_ticket_and_handoff.
        db.commit()
    else:
        ticket = create_escalation_ticket(
            tenant.id,
            primary_question_override or user_note or "(manual escalation)",
            trigger,
            db,
            chat_id=chat.id,
            session_id=session_id,
            user_context=effective,
            user_note=enriched_note,
            optional_entity_types=optional_entity_types,
        )
    if is_llm_unavailable:
        # LLM provider is the failing dependency — every step here must be
        # provably LLM-free. Resolve the response language from local signals
        # only (browser locale, then tenant escalation language); skip
        # resolve_language_context to avoid any current/future LLM-using
        # detection paths.
        tenant_profile = (
            db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant.id).first()
        )
        support_config = public_support_config_dict(
            tenant.settings if isinstance(tenant.settings, dict) else None
        )
        response_language = (
            (effective or {}).get("browser_locale")
            or support_config.get("escalation_language")
            or getattr(tenant_profile, "escalation_language", None)
        )
        message_to_user = support_notified_text(language=response_language)
        tokens_used = 0
    else:
        phase = (
            EscalationPhase.handoff_ask_email
            if not ticket.user_email
            else EscalationPhase.handoff_email_known
        )
        msgs = transcript_messages_for_openai(chat, optional_entity_types)
        tenant_profile = (
            db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant.id).first()
        )
        support_config = public_support_config_dict(
            tenant.settings if isinstance(tenant.settings, dict) else None
        )
        language_context = resolve_language_context(
            current_turn_text=user_note or "[User requested support via the Talk to support action.]",
            is_bootstrap_turn=False,
            bootstrap_user_locale=None,
            browser_locale=(effective or {}).get("browser_locale"),
            tenant_escalation_language=(
                support_config.get("escalation_language")
                or getattr(tenant_profile, "escalation_language", None)
            ),
            tenant_id=getattr(tenant, "public_id", None),
            chat_id=str(chat.id) if chat is not None else None,
        )
        out = await_only(
            complete_escalation_openai_turn(
                phase=phase,
                chat_messages=msgs,
                fact_json=fact_from_ticket(ticket, chat=chat),
                latest_user_text="[User requested support via the Talk to support action.]",
                api_key=api_key,
                response_language=language_context.response_language,
            )
        )
        message_to_user = out.message_to_user
        tokens_used = out.tokens_used
    if not ticket.user_email:
        chat.escalation_awaiting_ticket_id = ticket.id
    else:
        chat.escalation_followup_pending = True
    db.add(chat)
    db.commit()

    db.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content=message_to_user,
            source_documents=None,
        )
    )
    chat.tokens_used = int(chat.tokens_used or 0) + tokens_used
    db.add(chat)
    db.commit()

    # The runaway-loop detector must still see repeats that reuse collapses;
    # only the escalation count below is suppressed. Mirrors the FSM path.
    if reused:
        from backend.chat.events import _check_escalation_rate
        _check_escalation_rate(getattr(tenant, "public_id", None), bot_public_id)
    # Reuse is not a new escalation — emitting here would count one handed-off
    # conversation once per button press.
    if not reused:
        from backend.chat.events import _emit_chat_escalated_event
        _emit_chat_escalated_event(
            tenant_public_id=getattr(tenant, "public_id", None),
            bot_public_id=bot_public_id,
            chat_id=str(chat.id),
            escalation_reason=trigger.value,
            escalation_trigger=trigger.value,
            plan_tier=effective.get("plan_tier"),
            priority=ticket.priority.value,
        )

    return (message_to_user, ticket.ticket_number)


def chunks_preview_from_results(
    document_ids: list[uuid.UUID],
    scores: list[float],
    chunk_texts: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, doc_id in enumerate(document_ids):
        if i >= len(scores) or i >= len(chunk_texts):
            break
        text = chunk_texts[i]
        preview = text[:200] + ("..." if len(text) > 200 else "")
        out.append(
            {
                "document_id": str(doc_id),
                "score": float(scores[i]),
                "preview": preview,
            }
        )
    return out
