"""Session-level fallback for detected_language observability (ClickUp 86exmtu87).

Short follow-up turns ("Yes", "ok?") and locked chats (detection skipped)
yield detected_language="unknown" in trace metadata even though the user's
language is known from earlier turns. The fallback backfills metadata from
chats.last_detected_language and tags it detected_language_resolution_reason=
"session_fallback"; response_language resolution is never affected.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import ResolvedLanguageContext
from backend.chat.language_context import _apply_detected_language_session_fallback
from backend.chat.service import (
    process_chat_message,
)
from backend.models import Chat
from tests.test_language_sticky import (
    _chat_test_setup,
    _detection,
    _patch_process_chat_dependencies,
)


class _StubDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _context(
    *,
    detected_language: str,
    is_reliable: bool,
    confidence: float = 0.0,
    response_language: str = "ru",
    reason: str = "sticky_no_signal",
) -> ResolvedLanguageContext:
    return ResolvedLanguageContext(
        detected_language=detected_language,
        confidence=confidence,
        is_reliable=is_reliable,
        response_language=response_language,
        response_language_resolution_reason=reason,
        escalation_language="en",
        escalation_language_source="default",
    )


@pytest.mark.parametrize(
    "prior_detected_language, detected_language, is_reliable, confidence, "
    "expected_detected_language, expected_reason, expected_persisted",
    [
        pytest.param(
            None, "ru", True, 0.95, "ru", "detector", "ru", id="reliable_persists_last_detected_language"
        ),
        pytest.param(
            "ru", "unknown", False, 0.0, "ru", "session_fallback", None,
            id="unreliable_falls_back_to_session_language",
        ),
        pytest.param(
            None, "unknown", False, 0.0, "unknown", "detector", None,
            id="unreliable_without_prior_stays_unknown",
        ),
        pytest.param(
            "ru", "en", True, 0.92, "en", "detector", "en",
            id="new_reliable_detection_overwrites_stored_language",
        ),
    ],
)
def test_apply_detected_language_session_fallback(
    prior_detected_language: str | None,
    detected_language: str,
    is_reliable: bool,
    confidence: float,
    expected_detected_language: str,
    expected_reason: str,
    expected_persisted: str | None,
) -> None:
    db = _StubDB()
    chat = SimpleNamespace(last_detected_language=prior_detected_language, id=uuid.uuid4())

    result = _apply_detected_language_session_fallback(
        db=db,
        chat=chat,
        context=_context(detected_language=detected_language, is_reliable=is_reliable, confidence=confidence),
    )

    assert result.detected_language == expected_detected_language
    assert result.detected_language_resolution_reason == expected_reason
    if expected_persisted is None:
        assert db.added == []
    else:
        assert chat.last_detected_language == expected_persisted
        assert db.added == [chat]


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []

    def fake_capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("backend.chat.language_context.capture_event", fake_capture)
    return events


def _unknown_rate_events(events: list[tuple[str, dict]]) -> list[dict]:
    return [kwargs for event, kwargs in events if event == "chat_detected_language_unknown_rate"]


def test_chat_detected_language_metric_backfills_across_a_session(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    captured_events: list[tuple[str, dict]],
) -> None:
    """Guards, across one growing chat:
    - an unknown detection with no prior turn reports detected_language="unknown",
      is_unknown=True, reason="detector"
    - a reliable detection persists chats.last_detected_language and reports
      that language with reason="detector"
    - a later unreliable short follow-up ("Да") backfills detected_language from
      the session with reason="session_fallback" instead of surfacing "unknown"
    """
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "detected-fallback@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {
            "ok?": _detection("unknown"),
            "Привет мир": _detection("ru", confidence=0.9),
            "Да": _detection("unknown"),
        },
    )

    process_chat_message(tenant_id, "ok?", session_id, db_session, api_key=api_key)
    process_chat_message(tenant_id, "Привет мир", session_id, db_session, api_key=api_key)
    process_chat_message(tenant_id, "Да", session_id, db_session, api_key=api_key)

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.last_detected_language == "ru"

    metric_events = _unknown_rate_events(captured_events)
    assert len(metric_events) == 3
    first, second, third = (event["properties"] for event in metric_events)

    assert first["detected_language"] == "unknown"
    assert first["is_unknown"] is True
    assert first["detected_language_resolution_reason"] == "detector"

    assert second["detected_language"] == "ru"
    assert second["detected_language_resolution_reason"] == "detector"
    assert second["is_unknown"] is False

    assert third["detected_language_raw"] == "unknown"
    assert third["detected_language"] == "ru"
    assert third["detected_language_resolution_reason"] == "session_fallback"
    assert third["is_unknown"] is False
    assert third["chat_id"] == str(chat.id)


def test_chat_locked_chat_backfills_detected_language(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    captured_events: list[tuple[str, dict]],
) -> None:
    """Locked chats skip detection entirely (raw detected_language="unknown");
    the session fallback restores the pre-lock detected language in metadata."""
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "detected-locked@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        # First-turn non-English detection at >= 0.95 confidence locks the chat.
        {"Привет мир": _detection("ru", confidence=0.97)},
    )

    process_chat_message(tenant_id, "Привет мир", session_id, db_session, api_key=api_key)
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.language_locked is True

    process_chat_message(tenant_id, "Как проверить?", session_id, db_session, api_key=api_key)

    metric_events = _unknown_rate_events(captured_events)
    assert len(metric_events) == 2
    locked_turn = metric_events[1]["properties"]
    assert locked_turn["detected_language_raw"] == "unknown"
    assert locked_turn["detected_language"] == "ru"
    assert locked_turn["detected_language_resolution_reason"] == "session_fallback"
    assert locked_turn["response_language"] == "ru"
    assert locked_turn["response_language_resolution_reason"] == "locked"
