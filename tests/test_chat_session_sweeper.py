"""Unit tests for the inactive chat-session sweeper."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from backend.jobs import chat_session_sweeper
from backend.jobs.chat_session_sweeper import (
    auto_close_stale_tickets,
    sweep_inactive_chats,
)
from backend.models import (
    Chat,
    EscalationStatus,
    EscalationTicket,
    EscalationTrigger,
    Message,
    Tenant,
)
from backend.models.base import _utcnow
from backend.models.enums import MessageRole


@pytest.fixture(autouse=True)
def _pin_idle_timeout(monkeypatch):
    """Pin the sweep threshold to 30 min; the shipped window is 7 days."""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "conversation_idle_timeout_seconds", 1800)


def _make_tenant(db: Session) -> Tenant:
    tenant = Tenant(name="Sweeper Tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def _make_chat(
    db: Session,
    tenant: Tenant,
    *,
    age_minutes: int,
    first_message_role: MessageRole | None = MessageRole.user,
) -> Chat:
    created = _utcnow() - timedelta(minutes=age_minutes + 5)
    last_activity = _utcnow() - timedelta(minutes=age_minutes)
    chat = Chat(
        tenant_id=tenant.id,
        session_id=uuid.uuid4(),
        created_at=created,
        updated_at=last_activity,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    if first_message_role is not None:
        db.add(Message(chat_id=chat.id, role=first_message_role, content="hi"))
        db.commit()
    return chat


def test_sweep_cycle_marks_and_emits_correctly_across_mixed_chats(
    db_session: Session, monkeypatch
) -> None:
    """One sweep pass, seeded with every row shape the sweeper must tell apart:
    - a fresh (non-idle) chat is left untouched
    - an idle chat with messages is swept: emitted once, marker set,
      duration_ms matches its real activity window
    - an idle chat with no visitor message (empty, or greeting-only) is marked
      but never emitted. The check must correlate per row, not just "any
      message exists anywhere" (that bug would emit for these chats too)
    - a chat already carrying the marker is not re-swept/re-emitted
    - the marker write does not disturb updated_at — it is an analytics
      write, not activity, and rotation must still see the swept chat's real
      last-activity timestamp, not the marker commit's
    """
    tenant = _make_tenant(db_session)
    fresh = _make_chat(db_session, tenant, age_minutes=5)
    active = _make_chat(db_session, tenant, age_minutes=90)
    active_last_activity = active.updated_at
    empty = _make_chat(db_session, tenant, age_minutes=90, first_message_role=None)
    greeting_only = _make_chat(
        db_session, tenant, age_minutes=90, first_message_role=MessageRole.assistant
    )
    already_reported = _make_chat(db_session, tenant, age_minutes=90)
    already_reported.session_ended_event_at = already_reported.updated_at
    db_session.commit()

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 1
    swept_sessions = {c["session_id"] for c in captured}
    assert swept_sessions == {str(active.session_id)}

    db_session.expire_all()
    db_session.refresh(fresh)
    assert fresh.session_ended_event_at is None

    db_session.refresh(active)
    assert active.session_ended_event_at is not None
    assert active.updated_at == active_last_activity
    active_payload = next(c for c in captured if c["session_id"] == str(active.session_id))
    assert active_payload["tenant_public_id"] == tenant.public_id
    assert active_payload["outcome"] == "timeout"
    assert active_payload["duration_ms"] == int(
        (active_last_activity - active.created_at).total_seconds() * 1000
    )

    db_session.refresh(empty)
    db_session.refresh(greeting_only)
    assert empty.session_ended_event_at is not None
    assert greeting_only.session_ended_event_at is not None


def test_sweep_is_capped_and_drains_oldest_first(
    db_session: Session, monkeypatch
) -> None:
    tenant = _make_tenant(db_session)
    # Three inactive chats with distinct last-activity ages (oldest first).
    oldest = _make_chat(db_session, tenant, age_minutes=120)
    middle = _make_chat(db_session, tenant, age_minutes=100)
    _make_chat(db_session, tenant, age_minutes=80)

    monkeypatch.setattr(chat_session_sweeper, "_MAX_SESSIONS_PER_SWEEP", 2)
    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 2
    swept_sessions = {c["session_id"] for c in captured}
    assert swept_sessions == {str(oldest.session_id), str(middle.session_id)}


# ---------------------------------------------------------------------------
# Ticket auto-close — Chat9 has no inbound channel, so an open ticket never
# leaves ``open`` unless a tenant clicks "Mark as resolved" in the dashboard.
# The sweeper ages them out on the shared conversation-over window.
# ---------------------------------------------------------------------------


def _make_ticket(
    db: Session,
    tenant: Tenant,
    chat: Chat | None,
    *,
    status: EscalationStatus = EscalationStatus.open,
    number: str = "ESC-0001",
) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=tenant.id,
        ticket_number=number,
        primary_question="my domain won't delegate",
        trigger=EscalationTrigger.user_request,
        status=status,
        chat_id=chat.id if chat is not None else None,
        session_id=chat.session_id if chat is not None else None,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def test_stale_open_ticket_is_auto_closed(db_session: Session) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=90)
    ticket = _make_ticket(db_session, tenant, chat)

    assert auto_close_stale_tickets(db_session) == 1

    db_session.refresh(ticket)
    assert ticket.status == EscalationStatus.auto_closed
    assert ticket.resolved_at is not None
    # Auto-close must not fabricate a resolution — nobody answered.
    assert ticket.resolution_text is None


def test_ticket_on_active_conversation_is_left_open(db_session: Session) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=5)
    ticket = _make_ticket(db_session, tenant, chat)

    assert auto_close_stale_tickets(db_session) == 0

    db_session.refresh(ticket)
    assert ticket.status == EscalationStatus.open


def test_auto_close_leaves_already_terminal_tickets_alone(db_session: Session) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=90)
    resolved = _make_ticket(
        db_session, tenant, chat, status=EscalationStatus.resolved, number="ESC-0001"
    )

    assert auto_close_stale_tickets(db_session) == 0
    db_session.refresh(resolved)
    assert resolved.status == EscalationStatus.resolved


def test_auto_close_skips_tickets_without_a_chat(db_session: Session) -> None:
    """Direct API creations have no conversation to age against."""
    tenant = _make_tenant(db_session)
    ticket = _make_ticket(db_session, tenant, None)

    assert auto_close_stale_tickets(db_session) == 0
    db_session.refresh(ticket)
    assert ticket.status == EscalationStatus.open
