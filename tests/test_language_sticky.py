from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import (
    LanguageDetectionResult,
    STICKY_WINDOW,
    _STICKY_SWITCH_MARGIN,
    _weighted_vote,
    resolve_language_context,
)
from backend.chat.service import (
    ChatPipelineResult,
    RetrievalContext,
    _load_recent_user_turn_texts,
    process_chat_message,
)
from backend.models import Chat, EscalationTicket, EscalationTrigger, Message, MessageRole
from backend.search.service import build_reliability_assessment
from tests.conftest import register_and_verify_user, set_client_openai_key


def _detection(language: str, *, reliable: bool = True, confidence: float | None = None) -> LanguageDetectionResult:
    if language == "unknown":
        return LanguageDetectionResult("unknown", 0.0, False)
    resolved_confidence = confidence
    if resolved_confidence is None:
        resolved_confidence = 0.99 if reliable else 0.4
    return LanguageDetectionResult(language, resolved_confidence, reliable)


def _detect_from_map(mapping: dict[str, LanguageDetectionResult]):
    def _fake_detect(text: str | None) -> LanguageDetectionResult:
        return mapping[(text or "").strip()]

    return _fake_detect


def test_bootstrap_turn_unchanged() -> None:
    context = resolve_language_context(
        current_turn_text="",
        is_bootstrap_turn=True,
        bootstrap_user_locale="fr-FR",
        browser_locale="de-DE",
        tenant_escalation_language="es",
    )

    assert context.response_language == "fr-FR"
    assert context.response_language_resolution_reason == "bootstrap_user_locale"
    assert context.detected_language == "unknown"


def test_first_non_bootstrap_turn_no_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: _detection("ru"),
    )

    context = resolve_language_context(
        current_turn_text="Как сбросить пароль?",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language=None,
        recent_user_turn_texts=["Как сбросить пароль?"],
    )

    assert context.response_language == "ru"
    assert context.response_language_resolution_reason == "detected"


def test_sticky_retains_on_single_outlier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "Traceback: ValueError": _detection("en"),
                "Как исправить?": _detection("ru"),
                "Привет": _detection("ru"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="Traceback: ValueError",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="ru",
        recent_user_turn_texts=["Traceback: ValueError", "Как исправить?", "Привет"],
    )

    assert context.response_language == "ru"
    assert context.response_language_resolution_reason == "sticky_retained"


def test_sticky_switches_on_two_consecutive_new_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "Нужна помощь": _detection("ru"),
                "Как сбросить": _detection("ru"),
                "Hello": _detection("en"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="Нужна помощь",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="en",
        recent_user_turn_texts=["Нужна помощь", "Как сбросить", "Hello"],
    )

    assert context.response_language == "ru"
    assert context.response_language_resolution_reason == "sticky_switched"


def test_sticky_no_signal_keeps_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "?": _detection("unknown"),
                "ok": _detection("unknown"),
                ".": _detection("unknown"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="?",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="ru",
        recent_user_turn_texts=["?", "ok", "."],
    )

    assert context.response_language == "ru"
    assert context.response_language_resolution_reason == "sticky_no_signal"


def test_sticky_no_signal_falls_back_english_when_no_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "?": _detection("unknown"),
                "ok": _detection("unknown"),
                ".": _detection("unknown"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="?",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language=None,
        recent_user_turn_texts=["?", "ok", "."],
    )

    assert context.response_language == "en"
    assert context.response_language_resolution_reason == "detector_unknown"


def test_margin_exactly_at_threshold_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "Bonjour": _detection("fr"),
                "Hola": _detection("unknown"),
                "Hello": _detection("es"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="Bonjour",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="es",
        recent_user_turn_texts=["Bonjour", "Hola", "Hello"],
    )

    assert context.response_language == "fr"
    assert context.response_language_resolution_reason == "sticky_switched"
    assert 3 - 1 == _STICKY_SWITCH_MARGIN


def test_margin_just_below_threshold_keeps_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "Как дела": _detection("ru"),
                "Hello": _detection("en"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="Как дела",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="en",
        recent_user_turn_texts=["Как дела", "Hello"],
    )

    assert context.response_language == "en"
    assert context.response_language_resolution_reason == "sticky_retained"


def test_language_root_collapse_zh_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "你好": _detection("zh-CN"),
                "再见": _detection("zh-TW"),
            }
        ),
    )

    winner, votes = _weighted_vote(["你好", "再见"])

    assert winner == "zh"
    assert votes == {"zh": 5}


def test_resolve_language_context_preserves_latest_variant_for_winning_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "最新消息": _detection("zh-Hant"),
                "你好": _detection("zh-CN"),
            }
        ),
    )

    context = resolve_language_context(
        current_turn_text="最新消息",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="en",
        recent_user_turn_texts=["最新消息", "你好"],
    )

    assert context.response_language == "zh-Hant"
    assert context.response_language_resolution_reason == "sticky_switched"


def test_window_truncation_limits_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(
            {
                "first": _detection("en"),
                "second": _detection("fr"),
                "third": _detection("ru"),
                "fourth": _detection("de"),
                "fifth": _detection("es"),
            }
        ),
    )

    winner, votes = _weighted_vote(["first", "second", "third", "fourth", "fifth"])

    assert winner == "en"
    assert votes == {"en": 3, "fr": 2, "ru": 1}
    assert len(votes) <= STICKY_WINDOW


def test_resolve_language_context_backwards_compatible_without_sticky_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: _detection("fr"),
    )

    context = resolve_language_context(
        current_turn_text="bonjour",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
    )

    assert context.response_language == "fr"
    assert context.response_language_resolution_reason == "detected"


class _FakeSpan:
    def end(self, **kwargs: object) -> None:
        return None


class _FakeTrace:
    def span(self, **kwargs: object) -> _FakeSpan:
        return _FakeSpan()

    def update(self, **kwargs: object) -> None:
        return None

    def promote(self, **kwargs: object) -> None:
        return None


def _make_pipeline_result_for_language(response_language: str) -> ChatPipelineResult:
    retrieval = RetrievalContext(
        chunk_texts=["reset password in settings"],
        document_ids=[uuid.uuid4()],
        scores=[0.9],
        mode="hybrid",
        best_rank_score=0.9,
        best_confidence_score=0.9,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.9, result_count=3),
    )
    return ChatPipelineResult(
        raw_answer=f"lang={response_language}",
        final_answer=f"lang={response_language}",
        tokens_used=3,
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        is_faq_direct=False,
        retrieval=retrieval,
        escalation_recommended=False,
        escalation_trigger=None,
    )


def _chat_test_setup(tenant: TestClient, db_session: Session, email: str) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Sticky Language Tenant"},
    )
    set_client_openai_key(tenant, token)
    return uuid.UUID(create_resp.json()["id"]), create_resp.json()["api_key"]


def _patch_process_chat_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    detection_map: dict[str, LanguageDetectionResult],
) -> None:
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: _FakeTrace())
    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda *args, **kwargs: type(
            "InjectionResult",
            (),
            {"detected": False, "level": None, "method": None, "score": None},
        )(),
    )
    async def _fake_async_pipeline(*args, **kwargs):
        return _make_pipeline_result_for_language(
            kwargs["language_context"].response_language
        )

    monkeypatch.setattr(
        "backend.chat.service.async_run_chat_pipeline",
        _fake_async_pipeline,
    )
    monkeypatch.setattr("backend.chat.service._try_ingest_gap_signal", lambda **kwargs: None)
    monkeypatch.setattr("backend.chat.service._trigger_log_analysis_threshold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        _detect_from_map(detection_map),
    )


def test_chat_persists_last_response_language(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "sticky-persist@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {"Нужна помощь": _detection("ru")},
    )

    outcome = process_chat_message(
        tenant_id,
        "Нужна помощь",
        session_id,
        db_session,
        api_key=api_key,
    )

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert outcome.text == "lang=ru"
    assert chat.last_response_language == "ru"


def test_chat_sticky_survives_outlier_turn(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "sticky-outlier@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {
            "Привет мир": _detection("ru"),
            "Traceback: ValueError": _detection("en"),
        },
    )

    first = process_chat_message(tenant_id, "Привет мир", session_id, db_session, api_key=api_key)
    second = process_chat_message(
        tenant_id,
        "Traceback: ValueError",
        session_id,
        db_session,
        api_key=api_key,
    )

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert first.text == "lang=ru"
    assert second.text == "lang=ru"
    assert chat.last_response_language == "ru"


def test_chat_switches_language_after_two_consistent_turns(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "sticky-switch@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {
            "Hello there": _detection("en"),
            "Как дела": _detection("ru"),
            "Нужна помощь": _detection("ru"),
        },
    )

    first = process_chat_message(tenant_id, "Hello there", session_id, db_session, api_key=api_key)
    second = process_chat_message(tenant_id, "Как дела", session_id, db_session, api_key=api_key)
    third = process_chat_message(tenant_id, "Нужна помощь", session_id, db_session, api_key=api_key)

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert first.text == "lang=en"
    assert second.text == "lang=en"
    assert third.text == "lang=ru"
    assert chat.last_response_language == "ru"


def test_chat_logs_response_language_changed_on_switch(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "sticky-log@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {
            "Hello there": _detection("en"),
            "Как дела": _detection("ru"),
            "Нужна помощь": _detection("ru"),
        },
    )

    with caplog.at_level("INFO"):
        process_chat_message(tenant_id, "Hello there", session_id, db_session, api_key=api_key)
        process_chat_message(tenant_id, "Как дела", session_id, db_session, api_key=api_key)
        process_chat_message(tenant_id, "Нужна помощь", session_id, db_session, api_key=api_key)

    records = [record for record in caplog.records if record.msg == "response_language_changed"]
    assert any(
        getattr(record, "previous", None) == "en"
        and getattr(record, "next", None) == "ru"
        and getattr(record, "reason", None) == "sticky_switched"
        for record in records
    )


def test_chat_escalation_uses_user_response_language(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression test for ClickUp 86excm5kz.

    When the chat pipeline triggers an escalation, the user-facing response
    must stay in the user's language (here: Russian). The handoff LLM call
    must receive ``response_language="ru"`` and the persisted turn language
    must also be ``ru`` — not the tenant-side ``escalation_language`` (which
    defaults to English and is for support-team artifacts only).
    """
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "sticky-escalate@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {"Как сбросить пароль": _detection("ru")},
    )

    async def _escalating_pipeline(*args, **kwargs) -> ChatPipelineResult:
        result = _make_pipeline_result_for_language(kwargs["language_context"].response_language)
        return ChatPipelineResult(
            raw_answer=result.raw_answer,
            final_answer=result.final_answer,
            tokens_used=result.tokens_used,
            strategy=result.strategy,
            reject_reason=result.reject_reason,
            is_reject=result.is_reject,
            is_faq_direct=result.is_faq_direct,
            retrieval=result.retrieval,
            escalation_recommended=True,
            escalation_trigger=type("Trigger", (), {"value": "user_request"})(),
        )

    monkeypatch.setattr("backend.chat.service.async_run_chat_pipeline", _escalating_pipeline)

    def _create_ticket(
        _tenant_id, _primary_question, _trigger, pipeline_db, **kwargs
    ) -> EscalationTicket:
        # Write via the session the pipeline passed in (the async path's
        # sync_session), not ``db_session`` — concurrent writes from two
        # SQLite connections would deadlock. The signature mirrors
        # ``create_escalation_ticket`` (tenant_id, primary_question, trigger,
        # db, **kwargs).
        ticket = EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-TEST",
            primary_question="Как сбросить пароль",
            trigger=EscalationTrigger.user_request,
            chat_id=kwargs.get("chat_id"),
            session_id=kwargs.get("session_id"),
        )
        target_db = pipeline_db if pipeline_db is not None else db_session
        target_db.add(ticket)
        target_db.flush()
        return ticket

    monkeypatch.setattr("backend.chat.service.create_escalation_ticket", _create_ticket)

    captured: dict[str, str] = {}

    def _fake_escalation_turn(**kwargs):
        captured["response_language"] = kwargs.get("response_language", "")
        return type(
            "EscalationOut",
            (),
            {"message_to_user": "Support will reach out", "tokens_used": 2},
        )()

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        _fake_escalation_turn,
    )
    monkeypatch.setattr("backend.chat.service.fact_from_ticket", lambda *args, **kwargs: {})
    monkeypatch.setattr("backend.chat.service.build_chat_messages_for_openai", lambda *args, **kwargs: [])

    with caplog.at_level("INFO"):
        process_chat_message(
            tenant_id,
            "Как сбросить пароль",
            session_id,
            db_session,
            api_key=api_key,
        )

    # The escalation LLM was told to write in the user's language, not English.
    assert captured["response_language"] == "ru"

    # The persisted turn language is the user's language; the change log uses
    # the normal detection reason, not a forced "escalation_override" reason.
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.last_response_language == "ru"

    records = [record for record in caplog.records if record.msg == "response_language_changed"]
    assert any(
        getattr(record, "next", None) == "ru"
        and getattr(record, "reason", None) != "escalation_override"
        for record in records
    )


# ---------------------------------------------------------------------------
# Language lock — regression suite for the "lock after 2 consistent turns +
# confidence gate on first turn (non-English only)" rule.
# ---------------------------------------------------------------------------


def test_chat_locks_on_first_high_confidence_non_english_turn(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High-confidence non-English first turn locks immediately."""
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "lock-first-ru@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {"Привет мир": _detection("ru")},
    )

    process_chat_message(tenant_id, "Привет мир", session_id, db_session, api_key=api_key)

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.last_response_language == "ru"
    assert chat.language_locked is True


def test_chat_does_not_lock_on_first_english_turn(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """English on the first turn is too easy for the heuristic to claim
    by accident (any pure-ASCII multi-token text falls back to en).
    Don't lock until a second consistent turn confirms English.
    """
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "lock-first-en@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {"Hello there": _detection("en")},
    )

    process_chat_message(tenant_id, "Hello there", session_id, db_session, api_key=api_key)

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.last_response_language == "en"
    assert chat.language_locked is False


def test_chat_locks_after_two_consistent_english_turns(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """English locks on the second consecutive English turn."""
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "lock-two-en@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {"Hello there": _detection("en"), "How are you": _detection("en")},
    )

    process_chat_message(tenant_id, "Hello there", session_id, db_session, api_key=api_key)
    chat_after_first = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat_after_first.language_locked is False

    process_chat_message(tenant_id, "How are you", session_id, db_session, api_key=api_key)
    chat_after_second = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat_after_second.language_locked is True
    assert chat_after_second.last_response_language == "en"


def test_chat_locked_chat_keeps_language_against_off_language_turn(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once locked, the chat sticks to its language even if the user
    sends a clear off-language message. This is the intended trade-off
    of the lock rule — bilingual mid-session switches require a new
    chat session.
    """
    tenant_id, api_key = _chat_test_setup(tenant, db_session, "locked-keeps@example.com")
    session_id = uuid.uuid4()
    _patch_process_chat_dependencies(
        monkeypatch,
        {
            "Привет мир": _detection("ru"),
            "Hello there everyone": _detection("en"),
        },
    )

    first = process_chat_message(tenant_id, "Привет мир", session_id, db_session, api_key=api_key)
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.language_locked is True
    assert first.text == "lang=ru"

    second = process_chat_message(
        tenant_id, "Hello there everyone", session_id, db_session, api_key=api_key
    )
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.language_locked is True
    assert chat.last_response_language == "ru"
    assert second.text == "lang=ru"


def test_resolve_language_context_skips_detection_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the chat is locked, resolve_language_context returns the
    stored response_language with reason="locked" and does not invoke
    the underlying detector at all.
    """
    detector_calls = {"count": 0}

    def _exploding_detector(_text: str | None) -> LanguageDetectionResult:
        detector_calls["count"] += 1
        raise AssertionError("detector must not be called when language is locked")

    monkeypatch.setattr("backend.chat.language.detect_language", _exploding_detector)

    context = resolve_language_context(
        current_turn_text="Some message in any language",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="es",
        recent_user_turn_texts=["Some message in any language"],
        language_locked=True,
    )

    assert context.response_language == "es"
    assert context.response_language_resolution_reason == "locked"
    assert detector_calls["count"] == 0


def test_load_recent_user_turn_texts_without_duplicating_current(
    tenant: TestClient,
    db_session: Session,
) -> None:
    tenant_id, _api_key = _chat_test_setup(tenant, db_session, "sticky-history@example.com")
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.flush()
    base_time = datetime.now(UTC)
    db_session.add_all(
        [
            Message(
                chat_id=chat.id,
                role=MessageRole.user,
                content="older question",
                content_original_encrypted="enc",
                content_redacted="older question",
                created_at=base_time - timedelta(minutes=2),
            ),
            Message(
                chat_id=chat.id,
                role=MessageRole.user,
                content="latest previous question",
                content_original_encrypted="enc",
                content_redacted="latest previous question",
                created_at=base_time - timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()

    texts = _load_recent_user_turn_texts(
        db_session,
        chat,
        "current question",
        limit=3,
    )

    assert texts == ["current question", "latest previous question", "older question"]


def test_load_recent_user_turn_texts_prefers_decrypted_original(
    tenant: TestClient,
    db_session: Session,
) -> None:
    tenant_id, _api_key = _chat_test_setup(tenant, db_session, "sticky-history-original@example.com")
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.flush()
    db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content="REDACTED",
            content_original_encrypted="encrypted-token",
            content_redacted="REDACTED",
        )
    )
    db_session.commit()

    original_decrypt = "backend.chat.language_context._decrypt_optional"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(original_decrypt, lambda _value: "Как сбросить пароль?")
        texts = _load_recent_user_turn_texts(
            db_session,
            chat,
            "current question",
            limit=2,
        )

    assert texts == ["current question", "Как сбросить пароль?"]


# ---------------------------------------------------------------------------
# Single-word hint suppression (from test_language_detection_hints)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_detection_cache():
    from backend.chat.language import _detect_language_cached

    _detect_language_cached.cache_clear()
    yield
    _detect_language_cached.cache_clear()


@pytest.mark.parametrize("word", ["hello", "pricing", "thanks"])
def test_single_english_hint_word_not_forced(word: str) -> None:
    from backend.chat.language import detect_language

    result = detect_language(word)
    assert not result.is_reliable or result.detected_language == "unknown"


@pytest.mark.parametrize(
    "word, expected_lang",
    [
        ("bonjour", "fr"),
        ("hola", "es"),
        ("hallo", "de"),
        ("obrigado", "pt"),
    ],
)
def test_single_non_english_hint_word_detected(word: str, expected_lang: str) -> None:
    from backend.chat.language import detect_language

    result = detect_language(word)
    assert result.detected_language == expected_lang
    assert result.is_reliable


@pytest.mark.parametrize("word", ["ok", "hi", "yes", "no"])
def test_short_single_word_unreliable(word: str) -> None:
    from backend.chat.language import detect_language

    result = detect_language(word)
    assert not result.is_reliable
