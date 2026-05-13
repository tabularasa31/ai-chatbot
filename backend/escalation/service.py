"""Escalation ticket orchestration (FI-ESC)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.chat.language import resolve_language_context
from backend.chat.pii import redact
from backend.contact_sessions.service import sync_user_session_identity
from backend.core.config import settings
from backend.core.crypto import decrypt_value, encrypt_value
from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
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
from backend.observability.cache_metrics import record_hit, record_miss
from backend.privacy_config import public_redaction_config_dict
from backend.support_config import public_support_config_dict

_HR_CACHE_NAME = "human_request"

logger = logging.getLogger(__name__)

ESCALATION_THRESHOLD = 0.45

_CLARIFY_KEY = "escalation_followup_clarify"

_HUMAN_REQUEST_TIMEOUT = 3.0
_HUMAN_REQUEST_CACHE_TTL = 5 * 60
_HUMAN_REQUEST_CACHE_MAX = 2048
_human_request_cache: dict[str, tuple[float, bool]] = {}


def _hr_cache_get(key: str) -> bool | None:
    item = _human_request_cache.get(key)
    if not item:
        record_miss(_HR_CACHE_NAME)
        return None
    expires_at, result = item
    if time.time() > expires_at:
        _human_request_cache.pop(key, None)
        record_miss(_HR_CACHE_NAME)
        return None
    record_hit(_HR_CACHE_NAME)
    return result


def _hr_cache_set(key: str, result: bool) -> None:
    if len(_human_request_cache) >= _HUMAN_REQUEST_CACHE_MAX and key not in _human_request_cache:
        expired = [k for k, v in _human_request_cache.items() if time.time() > v[0]]
        for k in expired[:max(1, len(expired))]:
            _human_request_cache.pop(k, None)
        if len(_human_request_cache) >= _HUMAN_REQUEST_CACHE_MAX:
            oldest = min(_human_request_cache.items(), key=lambda x: x[1][0])[0]
            _human_request_cache.pop(oldest, None)
    _human_request_cache[key] = (time.time() + _HUMAN_REQUEST_CACHE_TTL, result)


def _tenant_optional_entity_types(tenant: Tenant | None) -> set[str] | None:
    if not tenant:
        return None
    raw = tenant.settings if isinstance(tenant.settings, dict) else None
    cfg = public_redaction_config_dict(raw)
    return set(cfg["optional_entity_types"])


def _safe_message_content(message: Message) -> str:
    return message.content_redacted or message.content


def _safe_ticket_question(ticket: EscalationTicket) -> str:
    return ticket.primary_question_redacted or ticket.primary_question


def _decrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_value(value)
    except RuntimeError:
        logger.warning("Failed to decrypt stored escalation original content")
        return None


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


def detect_human_request(
    message: str,
    api_key: str,
    tenant_id: UUID | str | None = None,
    *,
    langfuse_observation: Any | None = None,
) -> bool:
    """Return True if the user is requesting to speak with a human agent.

    Uses LLM classification so it works across all languages. Falls back to
    False on timeout or error to avoid false-positive escalations.

    `tenant_id` partitions the in-memory result cache so tenants never read
    each other's classifications.
    """
    cache_key = hashlib.sha256(
        f"{tenant_id}:{message}".encode()
    ).hexdigest()
    cached = _hr_cache_get(cache_key)
    if cached is not None:
        return cached

    system_prompt = (
        "Decide whether the user is *currently* asking to be connected to a "
        "human agent / operator / live support person, RIGHT NOW.\n"
        "\n"
        "Return true when the user's intent is to hand the conversation off "
        "to a person this turn — they want a human, not a self-serve answer. "
        "Examples (illustrative, not exhaustive): "
        "\"I want to talk to a human\", \"connect me to support\", "
        "\"I need to speak with a person\", \"can someone help me?\", "
        "\"please escalate this\".\n"
        "\n"
        "Return false when the user is asking an *informational* question "
        "ABOUT support / contact options that the bot should answer from "
        "the documentation. Examples: "
        "\"how do I contact support?\", \"what's your support email?\", "
        "\"where can I find help?\", \"do you have a support team?\". "
        "These are knowledge questions — the user wants to know HOW to "
        "reach support, not be handed off this turn.\n"
        "\n"
        "Look at intent, not exact wording. The same rule applies in any "
        "language; treat the user's phrasing as a hint, not a template.\n"
        "\n"
        'Answer ONLY with JSON: {"human_request": true/false}'
    )

    def _call_llm() -> bool:
        client = get_openai_client(api_key)
        response = call_openai_with_retry(
            "detect_human_request",
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
        return bool(json.loads(raw).get("human_request", False))

    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(_call_llm)
    try:
        result = future.result(timeout=_HUMAN_REQUEST_TIMEOUT)
    except (TimeoutError, Exception):
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
        return False
    else:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass

    _hr_cache_set(cache_key, result)
    return result


def compute_priority(
    trigger: EscalationTrigger,
    plan_tier: str | None,
    user_context: dict | None,
) -> EscalationPriority:
    tier = (plan_tier or (user_context or {}).get("plan_tier") or "").lower()
    enterprise = tier in ("enterprise", "pro")

    if trigger == EscalationTrigger.user_request and enterprise:
        return EscalationPriority.critical
    if trigger == EscalationTrigger.user_request:
        return EscalationPriority.high
    if trigger in (EscalationTrigger.low_similarity, EscalationTrigger.no_documents) and enterprise:
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
        lines.append(f"{role}: {_safe_message_content(m)[:500]}")
    return "\n".join(lines)


def _full_transcript_from_chat(
    chat_id: uuid.UUID,
    db: Session,
    *,
    max_turns: int = 10,
    extra_user_turn: tuple[str, datetime] | None = None,
) -> list[tuple[str, str, datetime | None]] | None:
    """Last ``max_turns`` user/assistant pairs as ``(role, content, created_at)``.

    ``extra_user_turn`` lets callers append a user message that is not yet
    persisted (the current turn — escalation notifications fire *before*
    ``_persist_turn`` runs, so without this the transcript misses the very
    message that triggered the escalation). Skipped if the last DB row is
    already a user turn with the same content (defensive against double-add
    when persistence ordering changes later).
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
            out.append((role, _safe_message_content(m), m.created_at))
    if extra_user_turn is not None:
        text, when = extra_user_turn
        text = text.strip()
        if text and not (out and out[-1][0] == "user" and out[-1][1].strip() == text):
            out.append(("user", text, when))
    return out or None


_KYC_IDENTITY_KEYS = {"email", "name", "plan_tier", "user_id", "audience_tag", "locale", "browser_locale"}

# Width of the indent used to wrap multi-line transcript turns under the
# "  user: " / "  assistant: " label so subsequent lines stay aligned with the
# message text. Matches the longest label ("assistant"); shorter labels get a
# slight visual offset which is acceptable here.
_TRANSCRIPT_WRAP_INDENT = " " * 13


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
    question_text = _safe_ticket_question(ticket).strip()
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
        extra_turn = (latest_user_text, when)

    transcript: list[tuple[str, str, datetime | None]] | None = None
    if ticket.chat_id:
        transcript = _full_transcript_from_chat(
            ticket.chat_id,
            db,
            extra_user_turn=extra_turn,
        )
    if transcript:
        lines.append(sep)
        lines.append("CONVERSATION (UTC)")
        last_date: date | None = None
        for role, content, when in transcript:
            when_utc = _to_utc(when)
            if when_utc is not None:
                cur_date = when_utc.date()
                if last_date is not None and cur_date != last_date:
                    lines.append(f"  ── {cur_date.isoformat()} ──")
                last_date = cur_date
                prefix = when_utc.strftime("%H:%M")
            else:
                prefix = "  · "
            indented = content.replace("\n", "\n" + _TRANSCRIPT_WRAP_INDENT)
            lines.append(f"  {prefix}  {role}: {indented}")
        lines.append("")
    elif ticket.conversation_summary:
        lines.append(sep)
        lines.append("CONVERSATION")
        for raw_line in ticket.conversation_summary.splitlines():
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


def _notify_tenant_new_ticket(
    tenant: Tenant,
    ticket: EscalationTicket,
    db: Session,
    *,
    latest_user_text: str | None = None,
    latest_user_at: datetime | None = None,
) -> None:
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
        return

    user = db.query(User).filter(User.tenant_id == tenant.id, User.role == "owner").first()
    support_config = public_support_config_dict(tenant.settings if isinstance(tenant.settings, dict) else None)
    recipient = support_config["l2_email"] or (user.email if user and user.email else None)
    if not recipient:
        logger.warning("No escalation notification email configured for tenant_id=%s", tenant.id)
        return

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
        send_email(
            recipient,
            subject,
            body,
            reply_to=ticket.user_email,
            extra_headers=headers,
        )
    except Exception as e:
        logger.warning("Escalation email failed: %s", e)


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
    redaction = redact(primary_question, optional_entity_types=optional_entity_types)

    ticket: EscalationTicket | None = None
    for attempt in range(3):
        ticket_number = generate_ticket_number(tenant_id, db)
        ticket = EscalationTicket(
            tenant_id=tenant_id,
            ticket_number=ticket_number,
            primary_question=redaction.redacted_text[:8000],
            primary_question_original_encrypted=encrypt_value(primary_question[:8000]),
            primary_question_redacted=redaction.redacted_text[:8000],
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
    from datetime import datetime

    ticket.status = EscalationStatus.resolved
    ticket.resolution_text = resolution_text
    ticket.resolved_at = datetime.now(UTC)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def delete_ticket_original_content(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> tuple[EscalationTicket | None, int]:
    ticket = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.id == ticket_id, EscalationTicket.tenant_id == tenant_id)
        .first()
    )
    if not ticket:
        return None, 0
    if ticket.primary_question_original_encrypted is None:
        return ticket, 0
    ticket.primary_question_original_encrypted = None
    ticket.primary_question = ticket.primary_question_redacted or ""
    db.add(ticket)
    return ticket, 1


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


def transcript_messages_for_openai(chat: Chat) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    for m in sorted(chat.messages, key=lambda x: x.created_at or x.id):
        # Skip empty-content messages (defensive guard; bootstrap no longer persists
        # empty user messages, but old sessions may still have them in the DB).
        if not (m.content or "").strip():
            continue
        role = "user" if m.role == MessageRole.user else "assistant"
        msgs.append({"role": role, "content": _safe_message_content(m)})
    return msgs


def build_chat_messages_for_openai(chat: Chat, current_user_text: str) -> list[dict[str, str]]:
    msgs = transcript_messages_for_openai(chat)
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


def perform_manual_escalation(
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
    """
    Create ticket + OpenAI handoff; persist assistant message only (no user bubble).
    Returns (message_to_user, ticket_number).

    For ``trigger == EscalationTrigger.llm_unavailable`` the OpenAI handoff is
    skipped entirely (the LLM is the failing dependency) and the user-facing
    message is taken from the static i18n table. ``failure_type`` is recorded
    in ``user_note``; ``original_user_message`` becomes the ticket's
    ``primary_question``.
    """
    from backend.chat.llm_unavailable_copy import support_notified_text
    from backend.escalation.openai_escalation import complete_escalation_openai_turn
    from backend.models import Chat, EscalationPhase

    chat = (
        db.query(Chat)
        .filter(Chat.session_id == session_id, Chat.tenant_id == tenant.id)
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
        msgs = transcript_messages_for_openai(chat)
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
        out = complete_escalation_openai_turn(
            phase=phase,
            chat_messages=msgs,
            fact_json=fact_from_ticket(ticket, chat=chat),
            latest_user_text="[User requested support via the Talk to support action.]",
            api_key=api_key,
            response_language=language_context.response_language,
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
            content=redact(
                message_to_user,
                optional_entity_types=optional_entity_types,
            ).redacted_text,
            content_original_encrypted=encrypt_value(message_to_user),
            content_redacted=redact(
                message_to_user,
                optional_entity_types=optional_entity_types,
            ).redacted_text,
            source_documents=None,
        )
    )
    chat.tokens_used = int(chat.tokens_used or 0) + tokens_used
    db.add(chat)
    db.commit()

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
