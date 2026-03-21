"""Escalation ticket orchestration (FI-ESC)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.email.service import send_email
from backend.models import (
    Chat,
    Client,
    EscalationPriority,
    EscalationStatus,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    User,
    UserSession,
)

logger = logging.getLogger(__name__)

ESCALATION_THRESHOLD = 0.45

_CLARIFY_KEY = "escalation_followup_clarify"


def should_escalate(
    best_similarity_score: float | None,
    chunk_count: int,
    trigger_override: EscalationTrigger | None = None,
) -> tuple[bool, EscalationTrigger | None]:
    if trigger_override is not None:
        return True, trigger_override
    if chunk_count == 0:
        return True, EscalationTrigger.no_documents
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
        "поговорить с",
        "соедини с",
        "хочу с человеком",
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


def generate_ticket_number(client_id: uuid.UUID, db: Session) -> str:
    rows = (
        db.query(EscalationTicket.ticket_number)
        .filter(EscalationTicket.client_id == client_id)
        .all()
    )
    max_n = 0
    for (num,) in rows:
        if isinstance(num, str) and num.upper().startswith("ESC-"):
            try:
                max_n = max(max_n, int(num[4:]))
            except ValueError:
                continue
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
        lines.append(f"{role}: {m.content[:500]}")
    return "\n".join(lines)


def _notify_tenant_new_ticket(client: Client, ticket: EscalationTicket, db: Session) -> None:
    user = db.query(User).filter(User.id == client.user_id).first()
    if not user or not user.email:
        logger.warning("No tenant email for client_id=%s", client.id)
        return
    base = settings.FRONTEND_URL.rstrip("/")
    body = (
        f"A user question couldn't be answered by your bot.\n\n"
        f"Ticket: {ticket.ticket_number}\n"
        f"Question: {ticket.primary_question[:500]}\n"
        f"Trigger: {ticket.trigger.value}\n"
        f"Priority: {ticket.priority.value}\n"
        f"User: {ticket.user_email or 'anonymous'}\n\n"
        f"View in dashboard: {base}/escalations/{ticket.id}"
    )
    subject = f"[Chat9] New support ticket #{ticket.ticket_number} — {ticket.primary_question[:60]}"
    try:
        send_email(user.email, subject, body)
    except Exception as e:  # noqa: BLE001
        logger.warning("Escalation email failed: %s", e)


def create_escalation_ticket(
    client_id: uuid.UUID,
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
) -> EscalationTicket:
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise ValueError("client not found")

    ticket_number = generate_ticket_number(client_id, db)
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

    ticket = EscalationTicket(
        client_id=client_id,
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
    db.commit()
    db.refresh(ticket)

    try:
        _notify_tenant_new_ticket(client, ticket, db)
    except Exception as e:  # noqa: BLE001
        logger.warning("notify tenant failed (ticket still created): %s", e)

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
    db.commit()

    uid = ctx.get("user_id")
    if uid:
        row = (
            db.query(UserSession)
            .filter(UserSession.client_id == chat.client_id, UserSession.user_id == str(uid))
            .first()
        )
        if row:
            row.email = email
            db.add(row)
            db.commit()


def resolve_ticket(
    ticket_id: uuid.UUID,
    client_id: uuid.UUID,
    resolution_text: str,
    db: Session,
) -> EscalationTicket:
    ticket = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.id == ticket_id, EscalationTicket.client_id == client_id)
        .first()
    )
    if not ticket:
        raise ValueError("ticket not found")
    from datetime import datetime, timezone

    ticket.status = EscalationStatus.resolved
    ticket.resolution_text = resolution_text
    ticket.resolved_at = datetime.now(timezone.utc)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


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
        role = "user" if m.role == MessageRole.user else "assistant"
        msgs.append({"role": role, "content": m.content})
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
    client: Client,
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
        .filter(Chat.session_id == session_id, Chat.client_id == client.id)
        .first()
    )
    if not chat:
        raise ValueError("session not found")

    effective = dict(chat.user_context) if chat.user_context else {}
    ticket = create_escalation_ticket(
        client.id,
        user_note or "(manual escalation)",
        trigger,
        db,
        chat_id=chat.id,
        session_id=session_id,
        user_context=effective,
        user_note=user_note,
    )
    phase = (
        EscalationPhase.handoff_ask_email
        if not ticket.user_email
        else EscalationPhase.handoff_email_known
    )
    msgs = transcript_messages_for_openai(chat)
    out = complete_escalation_openai_turn(
        phase=phase,
        chat_messages=msgs,
        fact_json=fact_from_ticket(ticket, chat=chat),
        latest_user_text="[User requested support via the Talk to support action.]",
        api_key=api_key,
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
            content=out.message_to_user,
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
