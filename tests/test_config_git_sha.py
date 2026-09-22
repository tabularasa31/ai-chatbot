"""GIT_SHA normalization: an unexpanded Railway reference must never leak into
release/version telemetry (Langfuse, Sentry, PostHog)."""

from __future__ import annotations

import pytest

from backend.core.config import Settings


@pytest.mark.parametrize(
    ("native_sha", "expected"),
    [
        pytest.param("abc1234def5678", "abc1234def5678", id="native_sha_present"),
        pytest.param(None, None, id="native_sha_missing"),
    ],
)
def test_literal_railway_reference_falls_back_to_native_sha(
    monkeypatch: pytest.MonkeyPatch, native_sha: str | None, expected: str | None
) -> None:
    # Reproduces the prod bug: GIT_SHA set to the literal "$RAILWAY_GIT_COMMIT_SHA"
    # (Railway does not expand `$VAR`), while Railway still injects the native var.
    monkeypatch.setenv("GIT_SHA", "$RAILWAY_GIT_COMMIT_SHA")
    if native_sha is None:
        monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    else:
        monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", native_sha)

    assert Settings().git_sha == expected
