"""Conversation rotation: idle sessions open a new Chat row on return.

Covers the acceptance criteria of the rotation feature:
- idle past the threshold -> new Chat, same session_id, fresh per-conversation
  state (clarification budget, history, greeting);
- return within the window -> same Chat continues;
- a live escalation ticket awaiting email blocks rotation;
- stale escalation offers without a ticket do NOT block rotation;
- session-level reads (logs, deletion, inbox list) span all conversations.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from backend.chat.history_service import (
    delete_session_original_content,
    get_session_logs,
    list_chat_sessions,
)
from backend.chat.rotation import should_rotate
from backend.chat.service import _ensure_chat_async
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


def _make_ticket(db: Session, tenant: Tenant) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=tenant.id,
        ticket_number="ESC-0001",
        primary_question="Need human support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def _make_tenant(db: Session) -> Tenant:
    tenant = Tenant(name="Rotation Tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def _make_chat(
    db: Session,
    tenant: Tenant,
    *,
    idle_minutes: int,
    session_id: uuid.UUID | None = None,
    with_message: bool = True,
    **chat_fields,
) -> Chat:
    created = _utcnow() - timedelta(minutes=idle_minutes + 5)
    last_activity = _utcnow() - timedelta(minutes=idle_minutes)
    chat = Chat(
        tenant_id=tenant.id,
        session_id=session_id or uuid.uuid4(),
        created_at=created,
        updated_at=last_activity,
        **chat_fields,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    if with_message:
        db.add(
            Message(
                chat_id=chat.id,
                role=MessageRole.user,
                content="hi",
                created_at=last_activity,
            )
        )
        db.commit()
    return chat


# ---------------------------------------------------------------------------
# should_rotate unit behavior (default threshold: 1800s = 30 min)
# ---------------------------------------------------------------------------


def test_fresh_chat_does_not_rotate(db_session: Session) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, idle_minutes=5)
    assert should_rotate(chat) is False


def test_idle_chat_rotates(db_session: Session) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, idle_minutes=45)
    assert should_rotate(chat) is True


def test_live_ticket_awaiting_email_blocks_rotation(db_session: Session) -> None:
    # A created ticket still collecting the visitor's email must survive the
    # idle window: the returning user completes it in the old conversation.
    tenant = _make_tenant(db_session)
    ticket = _make_ticket(db_session, tenant)
    chat = _make_chat(
        db_session,
        tenant,
        idle_minutes=45,
        escalation_awaiting_ticket_id=ticket.id,
    )
    assert should_rotate(chat) is False


@pytest.mark.parametrize(
    "stale_flag",
    [
        {"escalation_pre_confirm_pending": True},
        {"escalation_awaiting_request": True},
        {"escalation_followup_pending": True},
    ],
)
def test_stale_offers_without_ticket_do_not_block_rotation(
    db_session: Session, stale_flag: dict
) -> None:
    # No ticket exists behind these pending questions; a returning visitor
    # must get a fresh conversation, not an answer parsed against yesterday's
    # "want a human?" prompt.
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, idle_minutes=45, **stale_flag)
    assert should_rotate(chat) is True


def test_closed_idle_chat_rotates(db_session: Session) -> None:
    # ended_at chats rotate too: a visitor returning past the window starts
    # fresh instead of hitting the "session closed" dead end.
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, idle_minutes=45, ended_at=_utcnow())
    assert should_rotate(chat) is True


def test_threshold_comes_from_settings(db_session: Session, monkeypatch) -> None:
    from backend.core.config import settings

    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, idle_minutes=45)
    monkeypatch.setattr(settings, "conversation_idle_timeout_seconds", 3600)
    assert should_rotate(chat) is False


# ---------------------------------------------------------------------------
# _ensure_chat_async integration
# ---------------------------------------------------------------------------


async def _make_async_tenant_and_chat(async_db_session, *, idle_minutes: int, **chat_fields):
    tenant = Tenant(name="Async Rotation Tenant")
    async_db_session.add(tenant)
    await async_db_session.commit()
    await async_db_session.refresh(tenant)

    created = _utcnow() - timedelta(minutes=idle_minutes + 5)
    last_activity = _utcnow() - timedelta(minutes=idle_minutes)
    chat = Chat(
        tenant_id=tenant.id,
        session_id=uuid.uuid4(),
        created_at=created,
        updated_at=last_activity,
        clarification_count=1,
        **chat_fields,
    )
    async_db_session.add(chat)
    await async_db_session.commit()
    async_db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content="old question",
            created_at=last_activity,
        )
    )
    await async_db_session.commit()
    return tenant, chat


@pytest.mark.asyncio
async def test_ensure_chat_reuses_within_idle_window(async_db_session) -> None:
    tenant, chat = await _make_async_tenant_and_chat(async_db_session, idle_minutes=5)

    resolved, _, _ = await _ensure_chat_async(
        async_db_session, tenant.id, chat.session_id, None, None, None
    )

    assert resolved.id == chat.id
    assert resolved.clarification_count == 1


@pytest.mark.asyncio
async def test_ensure_chat_rotates_idle_conversation(async_db_session) -> None:
    tenant, chat = await _make_async_tenant_and_chat(async_db_session, idle_minutes=45)

    resolved, _, _ = await _ensure_chat_async(
        async_db_session, tenant.id, chat.session_id, None, None, None
    )

    # New conversation: fresh row, same session, all per-conversation state
    # reset (empty history makes the greeting's is_new_session hold).
    assert resolved.id != chat.id
    assert resolved.session_id == chat.session_id
    assert resolved.clarification_count in (0, None)
    assert resolved.messages == []


@pytest.mark.asyncio
async def test_ensure_chat_carries_user_context_across_rotation(
    async_db_session,
) -> None:
    tenant, chat = await _make_async_tenant_and_chat(
        async_db_session, idle_minutes=45, user_context={"user_id": "u-1"}
    )

    resolved, effective_ctx, _ = await _ensure_chat_async(
        async_db_session, tenant.id, chat.session_id, None, None, None
    )

    assert resolved.id != chat.id
    # Visitor identity survives rotation even though conversation state resets.
    assert effective_ctx == {"user_id": "u-1"}


@pytest.mark.asyncio
async def test_ensure_chat_carries_prior_language_across_rotation(
    async_db_session,
) -> None:
    tenant, chat = await _make_async_tenant_and_chat(
        async_db_session, idle_minutes=45, last_response_language="ru"
    )

    resolved, _, prior_session_language = await _ensure_chat_async(
        async_db_session, tenant.id, chat.session_id, None, None, None
    )

    # Fresh conversation, but the language the visitor last spoke in bridges the
    # rotation so a bootstrap re-greeting can answer in it instead of English.
    assert resolved.id != chat.id
    assert resolved.last_response_language is None
    assert prior_session_language == "ru"


@pytest.mark.asyncio
async def test_ensure_chat_does_not_rotate_with_live_ticket(async_db_session) -> None:
    tenant, chat = await _make_async_tenant_and_chat(async_db_session, idle_minutes=45)
    ticket = EscalationTicket(
        tenant_id=tenant.id,
        ticket_number="ESC-0001",
        primary_question="Need human support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
    )
    async_db_session.add(ticket)
    await async_db_session.commit()
    chat.escalation_awaiting_ticket_id = ticket.id
    async_db_session.add(chat)
    await async_db_session.commit()

    resolved, _, _ = await _ensure_chat_async(
        async_db_session, tenant.id, chat.session_id, None, None, None
    )

    # The escalation FSM finishes collecting the email in the old chat.
    assert resolved.id == chat.id


@pytest.mark.asyncio
async def test_ensure_chat_picks_latest_of_many(async_db_session) -> None:
    # Pre-seeded rotated session: two chats, the lookup must take the newest
    # (scalar_one_or_none would raise MultipleResultsFound here).
    tenant, old_chat = await _make_async_tenant_and_chat(
        async_db_session, idle_minutes=200
    )
    newer = Chat(
        tenant_id=tenant.id,
        session_id=old_chat.session_id,
        created_at=_utcnow() - timedelta(minutes=5),
        updated_at=_utcnow() - timedelta(minutes=5),
    )
    async_db_session.add(newer)
    await async_db_session.commit()

    resolved, _, _ = await _ensure_chat_async(
        async_db_session, tenant.id, old_chat.session_id, None, None, None
    )

    assert resolved.id == newer.id


# ---------------------------------------------------------------------------
# Session-level reads span all conversations of the session
# ---------------------------------------------------------------------------


def _seed_rotated_session(db_session: Session) -> tuple[Tenant, Chat, Chat]:
    tenant = _make_tenant(db_session)
    session_id = uuid.uuid4()
    old = _make_chat(
        db_session, tenant, idle_minutes=120, session_id=session_id
    )
    new = _make_chat(
        db_session, tenant, idle_minutes=1, session_id=session_id
    )
    return tenant, old, new


def test_session_logs_cover_all_conversations_with_chat_id(
    db_session: Session,
) -> None:
    tenant, old, new = _seed_rotated_session(db_session)

    logs = get_session_logs(old.session_id, tenant.id, db_session)

    assert logs is not None
    assert len(logs) == 2
    # Chronological across conversations; last tuple element is chat_id.
    assert [row[-1] for row in logs] == [old.id, new.id]


def test_list_chat_sessions_groups_rotated_conversations(
    db_session: Session,
) -> None:
    tenant, old, new = _seed_rotated_session(db_session)

    summaries = list_chat_sessions(tenant.id, db_session)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.session_id == old.session_id
    assert summary.message_count == 2


def test_delete_original_content_covers_all_conversations(
    db_session: Session,
) -> None:
    tenant, old, new = _seed_rotated_session(db_session)
    for chat in (old, new):
        msg = (
            db_session.query(Message).filter(Message.chat_id == chat.id).one()
        )
        msg.content_original_encrypted = b"secret"
        msg.content_redacted = "redacted"
        db_session.add(msg)
    db_session.commit()

    _, deleted = delete_session_original_content(
        old.session_id, tenant.id, db_session
    )

    assert deleted == 2


def test_sweeper_marker_forces_rotation_despite_fresh_updated_at(
    db_session: Session,
) -> None:
    # The sweeper's marker commit used to refresh updated_at (onupdate),
    # making the idle chat look fresh right after a sweep. The marker itself
    # is the system's declaration that the conversation ended — rotate.
    tenant = _make_tenant(db_session)
    chat = _make_chat(
        db_session,
        tenant,
        idle_minutes=1,
        session_ended_event_at=_utcnow(),
    )
    assert should_rotate(chat) is True


def test_live_ticket_blocks_rotation_even_with_sweeper_marker(
    db_session: Session,
) -> None:
    tenant = _make_tenant(db_session)
    ticket = _make_ticket(db_session, tenant)
    chat = _make_chat(
        db_session,
        tenant,
        idle_minutes=45,
        session_ended_event_at=_utcnow(),
        escalation_awaiting_ticket_id=ticket.id,
    )
    assert should_rotate(chat) is False
