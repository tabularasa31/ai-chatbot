"""GIT_SHA normalization: an unexpanded Railway reference must never leak into
release/version telemetry (Langfuse, Sentry, PostHog)."""

from __future__ import annotations

import pytest

from backend.core.config import Settings


def test_literal_railway_reference_falls_back_to_native_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reproduces the prod bug: GIT_SHA set to the literal "$RAILWAY_GIT_COMMIT_SHA"
    # (Railway does not expand `$VAR`), while Railway still injects the native var.
    monkeypatch.setenv("GIT_SHA", "$RAILWAY_GIT_COMMIT_SHA")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc1234def5678")

    assert Settings().git_sha == "abc1234def5678"


def test_literal_reference_without_native_sha_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SHA", "$RAILWAY_GIT_COMMIT_SHA")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)

    assert Settings().git_sha is None
