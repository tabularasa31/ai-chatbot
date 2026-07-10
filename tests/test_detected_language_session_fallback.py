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

from backend.chat.language import LanguageDetectionResult, ResolvedLanguageContext
from backend.chat.language_context import _apply_detected_language_session_fallback
from backend.chat.service import process_chat_message
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


def test_reliable_detection_persists_last_detected_language() -> None:
    db = _StubDB()
    chat = SimpleNamespace(last_detected_language=None, id=uuid.uuid4())

    result = _apply_detected_language_session_fallback(
        db=db,
        chat=chat,
        context=_context(detected_language="ru", is_reliable=True, confidence=0.95),
    )

    assert chat.last_detected_language == "ru"
    assert db.added == [chat]
    assert result.detected_language == "ru"
    assert result.detected_language_resolution_reason == "detector"


def test_unreliable_turn_falls_back_to_session_language() -> None:
    db = _StubDB()
    chat = SimpleNamespace(last_detected_language="ru", id=uuid.uuid4())

    result = _apply_detected_language_session_fallback(
        db=db,
        chat=chat,
        context=_context(detected_language="unknown", is_reliable=False),
    )

    assert result.detected_language == "ru"
    assert result.detected_language_resolution_reason == "session_fallback"
    # Raw reliability signals are preserved so lock decisions and
    # response_language resolution stay untouched.
    assert result.is_reliable is False
    assert result.confidence == 0.0
    assert result.response_language == "ru"
    assert db.added == []


def test_unreliable_turn_without_prior_stays_unknown() -> None:
    db = _StubDB()
    chat = SimpleNamespace(last_detected_language=None, id=uuid.uuid4())

    result = _apply_detected_language_session_fallback(
        db=db,
        chat=chat,
        context=_context(detected_language="unknown", is_reliable=False),
    )

    assert result.detected_language == "unknown"
    assert result.detected_language_resolution_reason == "detector"
    assert db.added == []


def test_new_reliable_detection_overwrites_stored_language() -> None:
    db = _StubDB()
    chat = SimpleNamespace(last_detected_language="ru", id=uuid.uuid4())

    _apply_detected_language_session_fallback(
        db=db,
        chat=chat,
        context=_context(detected_language="en", is_reliable=True, confidence=0.92),
    )

    assert chat.last_detected_language == "en"
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


def test_chat_short_followup_backfills_detected_language(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    captured_events: list[tuple[str, dict]],
) -> None:
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "detected-fallback@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {
            "Привет мир": _detection("ru", confidence=0.9),
            "Да": _detection("unknown"),
        },
    )

    process_chat_message(tenant_id, "Привет мир", session_id, db_session, api_key=api_key)
    process_chat_message(tenant_id, "Да", session_id, db_session, api_key=api_key)

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.last_detected_language == "ru"

    metric_events = _unknown_rate_events(captured_events)
    assert len(metric_events) == 2
    first, second = (event["properties"] for event in metric_events)
    assert first["detected_language"] == "ru"
    assert first["detected_language_resolution_reason"] == "detector"
    assert first["is_unknown"] is False
    assert second["detected_language_raw"] == "unknown"
    assert second["detected_language"] == "ru"
    assert second["detected_language_resolution_reason"] == "session_fallback"
    assert second["is_unknown"] is False
    assert second["chat_id"] == str(chat.id)


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


def test_chat_unknown_without_prior_reports_unknown_metric(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    captured_events: list[tuple[str, dict]],
) -> None:
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "detected-unknown@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {"ok?": _detection("unknown")},
    )

    process_chat_message(tenant_id, "ok?", session_id, db_session, api_key=api_key)

    metric_events = _unknown_rate_events(captured_events)
    assert len(metric_events) == 1
    properties = metric_events[0]["properties"]
    assert properties["detected_language"] == "unknown"
    assert properties["is_unknown"] is True
    assert properties["detected_language_resolution_reason"] == "detector"


def test_detection_result_dataclass_default_reason() -> None:
    # Existing constructors that do not pass the new field keep working.
    context = _context(detected_language="en", is_reliable=True)
    assert context.detected_language_resolution_reason == "detector"
    assert isinstance(
        LanguageDetectionResult("en", 0.9, True), LanguageDetectionResult
    )
