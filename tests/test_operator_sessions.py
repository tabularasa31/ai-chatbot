"""Operator-session observability — one recorded stretch per handoff.

The stretch a human serves is the thing the handoff feature exists to
produce, and until this table it was reported nowhere: ``chat_session_ended``
fires once per chat, from ``chats.created_at``, and cannot be emitted a
second time without doubling session counts (see 1bd8bd5).

These tests cover the row's whole life — opened by either entry point,
stamped once by the first human reply, closed by each of the paths that hand
a chat back, and repeated when an operator takes the same chat twice — plus
the emitted event, the first-response clock's anchor, and the sweeper
reconciliation pass that must not disturb the four passes it runs behind.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from backend.chat.handlers.operator import release_to_bot
from backend.jobs.chat_session_sweeper import (
    auto_close_stale_tickets,
    close_orphaned_operator_sessions,
    release_idle_operator_chats,
)
from backend.models import (
    Chat,
    EscalationStatus,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    OperatorSession,
    OperatorSessionEndReason,
    OperatorState,
    Tenant,
    User,
)
from backend.models.base import _utcnow
from backend.operator.service import (
    OperatorActor,
    OperatorChannel,
    claim_chat,
    ingest_from_operator,
    release_chat,
)
from backend.operator.sessions import get_open_operator_session

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
def tenant_row(db_session: Session) -> Tenant:
    tenant = Tenant(name="Operator Sessions Co")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture
def operator_user(db_session: Session, tenant_row: Tenant) -> User:
    user = User(
        email="op@example.com",
        password_hash="x",
        role="owner",
        is_verified=True,
        tenant_id=tenant_row.id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    """Every ``operator_session_ended`` payload, in emission order."""
    from backend.chat import events

    seen: list[dict] = []
    monkeypatch.setattr(
        events,
        "_emit_operator_session_ended_event",
        lambda **kwargs: seen.append(kwargs),
    )
    return seen


def _make_chat(db: Session, tenant: Tenant, **kwargs) -> Chat:
    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4(), **kwargs)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _make_ticket(
    db: Session,
    tenant: Tenant,
    chat: Chat,
    *,
    created_at=None,
    status: EscalationStatus = EscalationStatus.open,
) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=tenant.id,
        ticket_number=f"ESC-{uuid.uuid4().hex[:8]}",
        primary_question="my domain won't delegate",
        trigger=EscalationTrigger.user_request,
        status=status,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    if created_at is not None:
        ticket.created_at = created_at
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def _sessions(db: Session, chat: Chat) -> list[OperatorSession]:
    return (
        db.query(OperatorSession)
        .filter(OperatorSession.chat_id == chat.id)
        .order_by(OperatorSession.joined_at)
        .all()
    )


def _console(user: User | None) -> OperatorActor:
    return OperatorActor(
        channel=OperatorChannel.console,
        user_id=user.id if user is not None else None,
    )


# --------------------------------------------------------------------------
# Opening a stretch
# --------------------------------------------------------------------------


def test_take_opens_a_stretch_on_the_chats_own_join_stamp(
    db_session: Session, tenant_row: Tenant, operator_user: User
) -> None:
    chat = _make_chat(db_session, tenant_row)
    ticket = _make_ticket(db_session, tenant_row, chat)

    assert claim_chat(
        db_session,
        chat_id=chat.id,
        tenant_id=tenant_row.id,
        user_id=operator_user.id,
    )

    db_session.refresh(chat)
    (session,) = _sessions(db_session, chat)
    assert session.operator_user_id == operator_user.id
    assert session.ended_at is None
    assert session.first_reply_at is None
    # One clock, not two that can be read against each other.
    assert session.joined_at == chat.operator_joined_at
    # The ticket is resolved after mark_ticket_in_progress moved it, so the
    # in_progress status must not hide it from the lookup.
    db_session.refresh(ticket)
    assert ticket.status is EscalationStatus.in_progress
    assert session.escalation_ticket_id == ticket.id


def test_answering_an_unclaimed_chat_opens_and_stamps_in_one_go(
    db_session: Session, tenant_row: Tenant, operator_user: User
) -> None:
    """An operator who just starts typing never pressed "take"."""
    chat = _make_chat(db_session, tenant_row)
    _make_ticket(db_session, tenant_row, chat)

    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="Looking into it now.",
        actor=_console(operator_user),
    )

    (session,) = _sessions(db_session, chat)
    assert session.operator_user_id == operator_user.id
    assert session.first_reply_at is not None
    assert session.ended_at is None


def test_unattributed_reply_still_records_a_stretch(
    db_session: Session, tenant_row: Tenant
) -> None:
    """Phase 1's inbound e-mail from an address matching no tenant user."""
    chat = _make_chat(db_session, tenant_row)

    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="Fixed on our side.",
        actor=_console(None),
    )

    (session,) = _sessions(db_session, chat)
    assert session.operator_user_id is None
    assert session.first_reply_at is not None
    # Nobody escalated this conversation, so there is no ask to measure from.
    assert session.escalation_ticket_id is None


def test_second_reply_does_not_move_first_reply_at(
    db_session: Session, tenant_row: Tenant, operator_user: User
) -> None:
    chat = _make_chat(db_session, tenant_row)
    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="One moment.",
        actor=_console(operator_user),
    )
    (session,) = _sessions(db_session, chat)
    first = session.first_reply_at

    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="Here is the answer.",
        actor=_console(operator_user),
    )

    (session,) = _sessions(db_session, chat)
    assert session.first_reply_at == first


def test_colleague_answering_the_same_thread_shares_one_stretch(
    db_session: Session, tenant_row: Tenant, operator_user: User
) -> None:
    """Assignment is advisory: a shared inbox is one stretch, one clock."""
    chat = _make_chat(db_session, tenant_row)
    claim_chat(
        db_session,
        chat_id=chat.id,
        tenant_id=tenant_row.id,
        user_id=operator_user.id,
    )
    colleague = User(
        email="colleague@example.com",
        password_hash="x",
        role="owner",
        is_verified=True,
        tenant_id=tenant_row.id,
    )
    db_session.add(colleague)
    db_session.commit()
    db_session.refresh(colleague)
    db_session.refresh(chat)

    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="Jumping in here.",
        actor=_console(colleague),
    )

    (session,) = _sessions(db_session, chat)
    assert session.operator_user_id == operator_user.id
    assert session.first_reply_at is not None


# --------------------------------------------------------------------------
# Closing a stretch
# --------------------------------------------------------------------------


def test_explicit_release_closes_the_stretch_and_emits(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
) -> None:
    chat = _make_chat(db_session, tenant_row)
    asked_at = _utcnow() - timedelta(minutes=9)
    _make_ticket(db_session, tenant_row, chat, created_at=asked_at)
    claim_chat(
        db_session,
        chat_id=chat.id,
        tenant_id=tenant_row.id,
        user_id=operator_user.id,
    )
    db_session.refresh(chat)
    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="All sorted.",
        actor=_console(operator_user),
    )

    release_chat(db_session, chat)

    (session,) = _sessions(db_session, chat)
    assert session.ended_at is not None
    assert session.ended_reason is OperatorSessionEndReason.released
    assert session.ended_at == chat.operator_released_at

    (payload,) = captured
    assert payload["tenant_public_id"] == tenant_row.public_id
    assert payload["chat_id"] == str(chat.id)
    assert payload["session_id"] == str(chat.session_id)
    assert payload["operator_session_id"] == str(session.id)
    assert payload["operator_user_id"] == str(operator_user.id)
    assert payload["ended_reason"] == "released"
    assert payload["answered"] is True
    assert payload["duration_ms"] == int(
        (session.ended_at - session.joined_at).total_seconds() * 1000
    )
    # Measured from the ask, not from the moment the operator joined — the
    # two are seconds apart here and the metric would be meaningless.
    assert payload["first_response_ms"] == int(
        (session.first_reply_at - asked_at).total_seconds() * 1000
    )
    assert payload["first_response_ms"] >= 9 * 60 * 1000


def test_releasing_a_chat_already_in_bot_emits_nothing(
    db_session: Session, tenant_row: Tenant, captured: list[dict]
) -> None:
    chat = _make_chat(db_session, tenant_row)

    release_chat(db_session, chat)

    assert _sessions(db_session, chat) == []
    assert captured == []


def test_visitor_returning_closes_the_stretch_with_its_own_reason(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
) -> None:
    """The lazy release on the visitor's next turn.

    Distinct from ``idle_timeout``: the operator went quiet past the same
    window either way, but here the conversation was still going.
    """
    chat = _make_chat(db_session, tenant_row)
    claim_chat(
        db_session,
        chat_id=chat.id,
        tenant_id=tenant_row.id,
        user_id=operator_user.id,
    )
    db_session.refresh(chat)

    release_to_bot(
        db_session, chat, reason=OperatorSessionEndReason.visitor_returned
    )
    db_session.commit()

    (session,) = _sessions(db_session, chat)
    assert session.ended_reason is OperatorSessionEndReason.visitor_returned
    (payload,) = captured
    assert payload["ended_reason"] == "visitor_returned"
    # The operator never wrote: no reply to time, and nothing to report as one.
    assert payload["answered"] is False
    assert payload["first_response_ms"] is None


def test_sweeper_backstop_closes_the_stretch_nobody_returns_to(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
    monkeypatch,
) -> None:
    """The common case: an operator answers, the visitor leaves satisfied."""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "operator_release_idle_seconds", 900)
    long_ago = _utcnow() - timedelta(hours=3)
    chat = _make_chat(
        db_session,
        tenant_row,
        operator_state=OperatorState.live,
        assigned_operator_id=operator_user.id,
        operator_joined_at=long_ago,
    )
    ticket = _make_ticket(
        db_session, tenant_row, chat, created_at=long_ago - timedelta(minutes=4)
    )
    session = OperatorSession(
        tenant_id=tenant_row.id,
        chat_id=chat.id,
        operator_user_id=operator_user.id,
        escalation_ticket_id=ticket.id,
        joined_at=long_ago,
        first_reply_at=long_ago + timedelta(minutes=1),
    )
    db_session.add(session)
    db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.operator,
            content="Done — anything else?",
            created_at=long_ago + timedelta(minutes=1),
        )
    )
    db_session.commit()

    reference = _utcnow()
    assert release_idle_operator_chats(db_session, now=reference) == 1

    db_session.refresh(chat)
    assert chat.operator_state is OperatorState.bot
    (closed,) = _sessions(db_session, chat)
    assert closed.ended_reason is OperatorSessionEndReason.idle_timeout
    assert closed.ended_at == reference
    (payload,) = captured
    assert payload["ended_reason"] == "idle_timeout"
    assert payload["answered"] is True
    assert payload["first_response_ms"] == 5 * 60 * 1000


def test_taking_the_same_chat_twice_records_two_stretches(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
) -> None:
    """The shape a marker pair on ``chats`` would have destroyed."""
    chat = _make_chat(db_session, tenant_row)
    claim_chat(
        db_session,
        chat_id=chat.id,
        tenant_id=tenant_row.id,
        user_id=operator_user.id,
    )
    db_session.refresh(chat)
    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="First answer.",
        actor=_console(operator_user),
    )
    release_chat(db_session, chat)
    first_reply_at = _sessions(db_session, chat)[0].first_reply_at

    # The bot answers for a while, then the operator takes it back.
    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=tenant_row.id,
        text="Second answer.",
        actor=_console(operator_user),
    )
    release_chat(db_session, chat)

    first, second = _sessions(db_session, chat)
    assert first.id != second.id
    # The first stretch's record is untouched by the second takeover.
    assert first.first_reply_at == first_reply_at
    assert first.ended_at is not None and second.ended_at is not None
    assert first.ended_at <= second.joined_at
    assert [p["operator_session_id"] for p in captured] == [
        str(first.id),
        str(second.id),
    ]


# --------------------------------------------------------------------------
# The reconciliation pass
# --------------------------------------------------------------------------


def test_reconciliation_closes_an_orphan_at_the_chats_release_time(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
) -> None:
    """A release whose stretch-close did not land (a crash in between)."""
    joined = _utcnow() - timedelta(hours=2)
    released = _utcnow() - timedelta(hours=1)
    chat = _make_chat(
        db_session,
        tenant_row,
        operator_state=OperatorState.bot,
        operator_joined_at=joined,
        operator_released_at=released,
    )
    db_session.add(
        OperatorSession(
            tenant_id=tenant_row.id,
            chat_id=chat.id,
            operator_user_id=operator_user.id,
            joined_at=joined,
            first_reply_at=joined + timedelta(minutes=2),
        )
    )
    db_session.commit()

    assert close_orphaned_operator_sessions(db_session) == 1

    (session,) = _sessions(db_session, chat)
    assert session.ended_reason is OperatorSessionEndReason.reconciled
    # The stretch ended when the chat was handed back, not when we noticed.
    assert session.ended_at == released
    (payload,) = captured
    assert payload["duration_ms"] == int(
        (released - joined).total_seconds() * 1000
    )


def test_reconciliation_leaves_a_live_stretch_open(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
) -> None:
    chat = _make_chat(
        db_session,
        tenant_row,
        operator_state=OperatorState.live,
        assigned_operator_id=operator_user.id,
        operator_joined_at=_utcnow(),
    )
    db_session.add(
        OperatorSession(
            tenant_id=tenant_row.id,
            chat_id=chat.id,
            operator_user_id=operator_user.id,
            joined_at=_utcnow(),
        )
    )
    db_session.commit()

    assert close_orphaned_operator_sessions(db_session) == 0
    assert get_open_operator_session(db_session, chat_id=chat.id) is not None
    assert captured == []


def test_reconciliation_is_idempotent(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
) -> None:
    """A closed stretch is reported once, however often the sweeper runs."""
    joined = _utcnow() - timedelta(hours=2)
    chat = _make_chat(
        db_session,
        tenant_row,
        operator_joined_at=joined,
        operator_released_at=_utcnow() - timedelta(hours=1),
    )
    db_session.add(
        OperatorSession(
            tenant_id=tenant_row.id, chat_id=chat.id, joined_at=joined
        )
    )
    db_session.commit()

    assert close_orphaned_operator_sessions(db_session) == 1
    assert close_orphaned_operator_sessions(db_session) == 0
    assert len(captured) == 1


def test_reconciliation_does_not_disturb_ticket_auto_close(
    db_session: Session,
    tenant_row: Tenant,
    operator_user: User,
    captured: list[dict],
    monkeypatch,
) -> None:
    """It writes to ``operator_sessions`` only — no chat, no ticket.

    Pass 4 skips ``live`` chats and ages tickets on ``chats.updated_at``;
    neither may shift because a stretch was reconciled behind it.
    """
    from backend.core.config import settings

    monkeypatch.setattr(settings, "conversation_idle_timeout_seconds", 1800)
    joined = _utcnow() - timedelta(hours=4)
    idle_since = _utcnow() - timedelta(hours=3)
    chat = _make_chat(
        db_session,
        tenant_row,
        operator_joined_at=joined,
        operator_released_at=idle_since,
    )
    db_session.query(Chat).filter(Chat.id == chat.id).update(
        {"updated_at": idle_since}, synchronize_session=False
    )
    ticket = _make_ticket(db_session, tenant_row, chat)
    db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.operator,
            content="answered",
            created_at=joined,
        )
    )
    db_session.add(
        OperatorSession(
            tenant_id=tenant_row.id,
            chat_id=chat.id,
            operator_user_id=operator_user.id,
            joined_at=joined,
            first_reply_at=joined,
        )
    )
    db_session.commit()

    assert close_orphaned_operator_sessions(db_session) == 1
    assert len(captured) == 1

    db_session.refresh(chat)
    assert chat.updated_at == idle_since
    assert auto_close_stale_tickets(db_session) == 1
    db_session.refresh(ticket)
    assert ticket.status is EscalationStatus.auto_closed
