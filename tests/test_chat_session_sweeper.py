"""Unit tests for the inactive chat-session sweeper."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.jobs import chat_session_sweeper
from backend.jobs.chat_session_sweeper import sweep_inactive_chats
from backend.models import Chat, Tenant
from backend.models.base import _utcnow


def _make_tenant(db: Session) -> Tenant:
    tenant = Tenant(name="Sweeper Tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def _make_chat(db: Session, tenant: Tenant, *, age_minutes: int) -> Chat:
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
    return chat


def test_sweeps_inactive_chat_and_emits_event(
    db_session: Session, monkeypatch
) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=90)
    last_activity = chat.updated_at

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 1
    db_session.refresh(chat)
    # Marker is set, but ended_at stays NULL so the chat remains resumable.
    assert chat.session_ended_event_at is not None
    assert chat.ended_at is None

    assert len(captured) == 1
    payload = captured[0]
    assert payload["tenant_public_id"] == tenant.public_id
    assert payload["session_id"] == str(chat.session_id)
    assert payload["outcome"] == "timeout"
    assert payload["duration_ms"] == int(
        (last_activity - chat.created_at).total_seconds() * 1000
    )


def test_fresh_chat_is_not_swept(db_session: Session, monkeypatch) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=5)

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 0
    assert captured == []
    db_session.refresh(chat)
    assert chat.session_ended_event_at is None


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


def test_already_reported_chat_is_not_re_emitted(
    db_session: Session, monkeypatch
) -> None:
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=90)
    chat.session_ended_event_at = chat.updated_at
    db_session.commit()

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 0
    assert captured == []


def test_escalation_closed_chat_is_skipped(
    db_session: Session, monkeypatch
) -> None:
    # Chats closed by escalation (ended_at set) already emit their own event;
    # the sweeper must not emit a second "timeout" event for them.
    tenant = _make_tenant(db_session)
    chat = _make_chat(db_session, tenant, age_minutes=90)
    chat.ended_at = chat.updated_at
    db_session.commit()

    captured: list[dict] = []
    monkeypatch.setattr(
        chat_session_sweeper,
        "_emit_chat_session_ended_event",
        lambda **kwargs: captured.append(kwargs),
    )

    count = sweep_inactive_chats(db_session)

    assert count == 0
    assert captured == []
    db_session.refresh(chat)
    assert chat.session_ended_event_at is None
