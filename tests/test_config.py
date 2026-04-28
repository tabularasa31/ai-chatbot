from __future__ import annotations

from backend.core.config import Settings


def test_guard_models_default_to_lightweight_models() -> None:
    settings = Settings()

    assert settings.human_request_model == "gpt-4o-mini"
    assert settings.relevance_guard_model == "gpt-4o-mini"
    assert settings.answer_validation_model == "gpt-4o-mini"


def test_guard_models_roll_back_with_explicit_env_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RELEVANCE_GUARD_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("VALIDATION_MODEL", "gpt-4.1-mini")

    settings = Settings()

    assert settings.relevance_guard_model == "gpt-4.1-mini"
    assert settings.answer_validation_model == "gpt-4.1-mini"


def test_human_request_model_env_does_not_override_scoped_guard_models(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HUMAN_REQUEST_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("ANSWER_VALIDATION_MODEL", "gpt-4.1-mini")

    settings = Settings()

    assert settings.human_request_model == "gpt-4.1-mini"
    assert settings.relevance_guard_model == "gpt-4o-mini"
    assert settings.answer_validation_model == "gpt-4o-mini"
