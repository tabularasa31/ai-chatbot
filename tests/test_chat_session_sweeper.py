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
    """Pin both sweep thresholds to 30 min for these tests.

    The shipped conversation window is 7 days (shared with rotation); empty
    mount chats are reaped on a separate short window. These tests exercise the
    sweep *mechanism* at a single fixed threshold; the decoupled behavior is
    covered explicitly by ``test_empty_chat_reaped_on_short_window_...``.
    """
    from backend.core.config import settings

    monkeypatch.setattr(settings, "conversation_idle_timeout_seconds", 1800)
    monkeypatch.setattr(settings, "empty_chat_idle_timeout_seconds", 1800)


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
    with_message: bool = True,
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
    if with_message:
        db.add(Message(chat_id=chat.id, role=MessageRole.user, content="hi"))
        db.commit()
    return chat


def test_sweep_cycle_marks_and_emits_correctly_across_mixed_chats(
    db_session: Session, monkeypatch
) -> None:
    """One sweep pass, seeded with every row shape the sweeper must tell apart:
    - a fresh (non-idle) chat is left untouched
    - an idle chat with messages is swept: emitted once, marker set,
      ended_at stays NULL, duration_ms matches its real activity window
    - an idle *empty* mount chat (no messages) is marked but never emitted.
      /widget/session/init creates a Chat row on every widget mount before the
      user writes anything; emitting chat_session_ended for those inflated the
      funnel with widget-impressions (observed 154 "sessions" per 1 real turn).
      The has_messages check must also correlate per row, not just "any
      message exists anywhere" (that bug would emit for the empty chat too)
    - a chat already carrying the marker is not re-swept/re-emitted
    - a legacy row closed via ``ended_at`` is swept like any other idle chat
      (the sweeper ignores ``ended_at``; in practice this only reaches a
      legacy row the ``legacy_closed_chats_marker_v1`` backfill never saw)
    - the marker write does not disturb updated_at — it is an analytics
      write, not activity, and rotation must still see the swept chat's real
      last-activity timestamp, not the marker commit's
    """
    tenant = _make_tenant(db_session)
    fresh = _make_chat(db_session, tenant, age_minutes=5)
    active = _make_chat(db_session, tenant, age_minutes=90)
    active_last_activity = active.updated_at
    empty = _make_chat(db_session, tenant, age_minutes=90, with_message=False)
    already_reported = _make_chat(db_session, tenant, age_minutes=90)
    already_reported.session_ended_event_at = already_reported.updated_at
    db_session.commit()
    legacy = _make_chat(db_session, tenant, age_minutes=90)
    # Query-level update: a plain ORM commit would fire updated_at's onupdate
    # and make the idle chat look fresh.
    db_session.query(Chat).filter(Chat.id == legacy.id).update(
        {"ended_at": legacy.updated_at, "updated_at": legacy.updated_at},
        synchronize_session=False,
    )
    db_session.commit()

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 2
    swept_sessions = {c["session_id"] for c in captured}
    assert swept_sessions == {str(active.session_id), str(legacy.session_id)}

    db_session.expire_all()
    db_session.refresh(fresh)
    assert fresh.session_ended_event_at is None

    db_session.refresh(active)
    assert active.session_ended_event_at is not None
    assert active.ended_at is None
    assert active.updated_at == active_last_activity
    active_payload = next(c for c in captured if c["session_id"] == str(active.session_id))
    assert active_payload["tenant_public_id"] == tenant.public_id
    assert active_payload["outcome"] == "timeout"
    assert active_payload["duration_ms"] == int(
        (active_last_activity - active.created_at).total_seconds() * 1000
    )

    db_session.refresh(empty)
    assert empty.session_ended_event_at is not None

    db_session.refresh(legacy)
    assert legacy.session_ended_event_at is not None


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


def test_empty_chat_reaped_on_short_window_message_chat_kept(
    db_session: Session, monkeypatch
) -> None:
    """Decoupled windows: an idle empty mount chat is reaped on the short
    empty-chat window while a message-bearing conversation in the same idle
    range is kept for the long conversation window (returning-visitor
    continuity). Guards against empty mount chats lingering in
    ix_chats_sweeper_pending for days once the conversation window is raised."""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "conversation_idle_timeout_seconds", 7 * 24 * 3600)
    monkeypatch.setattr(settings, "empty_chat_idle_timeout_seconds", 1800)

    tenant = _make_tenant(db_session)
    # 45 min idle: past the 30-min empty window, well within the 7-day one.
    empty = _make_chat(db_session, tenant, age_minutes=45, with_message=False)
    conversation = _make_chat(db_session, tenant, age_minutes=45, with_message=True)

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    # Empty chat reaped (marker set, no event); conversation left pending.
    assert count == 0
    assert captured == []
    db_session.refresh(empty)
    db_session.refresh(conversation)
    assert empty.session_ended_event_at is not None
    assert conversation.session_ended_event_at is None


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


def test_auto_close_covers_legacy_ended_at_chats(db_session: Session) -> None:
    """Tickets on legacy closed rows age out on the same idle rule."""
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=90)
    # Query-level update with an explicit updated_at: a plain ORM commit would
    # fire the column's onupdate and make the idle chat look fresh.
    db_session.query(Chat).filter(Chat.id == chat.id).update(
        {"ended_at": chat.updated_at, "updated_at": chat.updated_at},
        synchronize_session=False,
    )
    db_session.commit()
    ticket = _make_ticket(db_session, tenant, chat)

    assert auto_close_stale_tickets(db_session) == 1
    db_session.refresh(ticket)
    assert ticket.status == EscalationStatus.auto_closed


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
