"""Escalation ticket orchestration (FI-ESC)."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from backend.chat.language import resolve_language_context
from backend.chat.pii import redact
from backend.contact_sessions.service import sync_user_session_identity
from backend.core.config import settings
from backend.core.crypto import decrypt_value, encrypt_value
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
from backend.privacy_config import public_redaction_config_dict
from backend.support_config import public_support_config_dict

logger = logging.getLogger(__name__)

ESCALATION_THRESHOLD = 0.45

_CLARIFY_KEY = "escalation_followup_clarify"


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
    validation: dict[str, Any] | None = None,
    trigger_override: EscalationTrigger | None = None,
) -> tuple[bool, EscalationTrigger | None]:
    if trigger_override is not None:
        return True, trigger_override
    if chunk_count == 0:
        return True, EscalationTrigger.no_documents
    if validation and validation.get("is_valid") is True:
        return False, None
    if best_similarity_score is None or best_similarity_score < ESCALATION_THRESHOLD:
        return True, EscalationTrigger.low_similarity
    return False, None


def detect_human_request(message: str) -> bool:
    t = message.lower()
    patterns = (
        "talk to a human",
        "talk to human",
        "speak to a human",
        "speak to human",
        "connect me to",
        "get me a human",
        "i want a human",
        "i want an agent",
        "i want a person",
        "live agent",
        "real person",
        "human agent",
        "human support",
        "поговорить с",  # noqa: RUF001
        "соедини с",  # noqa: RUF001
        "хочу с человеком",  # noqa: RUF001
        "оператор",
        "живой человек",
    )
    if any(p in t for p in patterns):
        return True
    if ("human" in t or "agent" in t or "support" in t) and (
        "not helpful" in t or "useless" in t or "this is useless" in t
    ):
        return True
    return False


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


def _notify_tenant_new_ticket(tenant: Tenant, ticket: EscalationTicket, db: Session) -> None:
    user = db.query(User).filter(User.tenant_id == tenant.id, User.role == "owner").first()
    support_config = public_support_config_dict(tenant.settings if isinstance(tenant.settings, dict) else None)
    recipient = support_config["l2_email"] or (user.email if user and user.email else None)
    if not recipient:
        logger.warning("No escalation notification email configured for tenant_id=%s", tenant.id)
        return
    base = settings.FRONTEND_URL.rstrip("/")
    body = (
        f"A user question couldn't be answered by your bot.\n\n"
        f"Ticket: {ticket.ticket_number}\n"
        f"Question: {_safe_ticket_question(ticket)[:500]}\n"
        f"Trigger: {ticket.trigger.value}\n"
        f"Priority: {ticket.priority.value}\n"
        f"User: {ticket.user_email or 'anonymous'}\n\n"
        f"View in dashboard: {base}/escalations/{ticket.id}"
    )
    subject = f"[Chat9] New support ticket #{ticket.ticket_number} — {_safe_ticket_question(ticket)[:60]}"
    try:
        send_email(recipient, subject, body)
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
        _notify_tenant_new_ticket(tenant, ticket, db)
    except Exception as e:
        logger.warning("notify tenant owner failed (ticket still created): %s", e)

    return ticket


_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)


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
) -> None:
    ticket = db.query(EscalationTicket).filter(EscalationTicket.id == ticket_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not ticket or not chat:
        return
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
) -> tuple[str, str]:
    """
    Create ticket + OpenAI handoff; persist assistant message only (no user bubble).
    Returns (message_to_user, ticket_number).
    """
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
    ticket = create_escalation_ticket(
        tenant.id,
        user_note or "(manual escalation)",
        trigger,
        db,
        chat_id=chat.id,
        session_id=session_id,
        user_context=effective,
        user_note=user_note,
        optional_entity_types=optional_entity_types,
    )
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
        escalation_language=language_context.escalation_language,
    )
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
                out.message_to_user,
                optional_entity_types=optional_entity_types,
            ).redacted_text,
            content_original_encrypted=encrypt_value(out.message_to_user),
            content_redacted=redact(
                out.message_to_user,
                optional_entity_types=optional_entity_types,
            ).redacted_text,
            source_documents=None,
        )
    )
    chat.tokens_used = int(chat.tokens_used or 0) + out.tokens_used
    db.add(chat)
    db.commit()
    return (out.message_to_user, ticket.ticket_number)


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
