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
    assert chat.ended_at == last_activity

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
    assert chat.ended_at is None


def test_already_ended_chat_is_not_re_emitted(
    db_session: Session, monkeypatch
) -> None:
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
