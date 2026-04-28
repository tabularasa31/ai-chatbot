from __future__ import annotations

from backend.core.config import Settings


def test_guard_models_default_to_lightweight_models() -> None:
    settings = Settings()

    assert settings.relevance_guard_model == "gpt-4o-mini"
    assert settings.answer_validation_model == "gpt-4o-mini"


def test_guard_models_can_roll_back_with_legacy_env_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GUARDS_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("ANSWER_VALIDATION_MODEL", "gpt-4.1-mini")

    settings = Settings()

    assert settings.relevance_guard_model == "gpt-4.1-mini"
    assert settings.answer_validation_model == "gpt-4.1-mini"


def test_guard_models_prefer_new_env_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GUARDS_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("ANSWER_VALIDATION_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("RELEVANCE_GUARD_MODEL", "gpt-test-relevance")
    monkeypatch.setenv("VALIDATION_MODEL", "gpt-test-validation")

    settings = Settings()

    assert settings.relevance_guard_model == "gpt-test-relevance"
    assert settings.answer_validation_model == "gpt-test-validation"
