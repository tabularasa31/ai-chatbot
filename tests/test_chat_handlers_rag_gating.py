"""Unit tests for the query-rewrite gating predicate.

Covers each branch of ``_should_skip_query_rewrite`` so we can refactor the
heuristic without breaking the latency/recall trade-off documented in
ClickUp 86exdq7wp.
"""

from __future__ import annotations

import pytest

from backend.chat.handlers.rag import _should_skip_query_rewrite


@pytest.mark.parametrize(
    "language_match",
    ["mismatch", "unknown"],
)
def test_skip_runs_rewrite_on_non_native_language(language_match: str) -> None:
    skip, reason = _should_skip_query_rewrite(
        "this is a long enough question without any abbreviations whatsoever",
        language_match=language_match,
        min_words=4,
    )
    assert skip is False
    assert reason == "language_mismatch"


def test_skip_runs_rewrite_on_short_query() -> None:
    skip, reason = _should_skip_query_rewrite(
        "how to pay",
        language_match="native",
        min_words=4,
    )
    assert skip is False
    assert reason == "short_query"


@pytest.mark.parametrize(
    "question",
    [
        "how do I configure SLA notifications for my account",
        "tell me about API rate limits in the dashboard",
        "what is the VPN setup procedure for new users",
        "how do I onboard B2B clients into the workspace",
        "set up 2FA for the admin account please",
        "where is the S3 bucket configuration in dashboard",
    ],
)
def test_skip_runs_rewrite_when_abbreviation_present(question: str) -> None:
    skip, reason = _should_skip_query_rewrite(
        question,
        language_match="native",
        min_words=4,
    )
    assert skip is False
    assert reason == "has_abbreviation"


@pytest.mark.parametrize(
    "question",
    [
        "I have 100 orders pending in the queue today",
        "show me invoices from 2023 in the dashboard",
    ],
)
def test_skip_ignores_pure_numeric_tokens(question: str) -> None:
    # Pure digits must not be treated as acronyms — otherwise routine queries
    # mentioning counts or years would never get the latency win.
    skip, reason = _should_skip_query_rewrite(
        question,
        language_match="native",
        min_words=4,
    )
    assert skip is True
    assert reason == "eligible_to_skip"


@pytest.mark.parametrize(
    "question",
    [
        "how do I reset my password from the settings page",
        "where can I find the invoice for last month",
        "explain how to switch my subscription plan",
    ],
)
def test_skip_skips_eligible_native_long_no_abbr(question: str) -> None:
    skip, reason = _should_skip_query_rewrite(
        question,
        language_match="native",
        min_words=4,
    )
    assert skip is True
    assert reason == "eligible_to_skip"


def test_min_words_threshold_is_inclusive() -> None:
    # Exactly 4 words → eligible.
    skip_at_threshold, _ = _should_skip_query_rewrite(
        "reset my password please",
        language_match="native",
        min_words=4,
    )
    assert skip_at_threshold is True

    # 3 words → short.
    skip_below_threshold, reason = _should_skip_query_rewrite(
        "reset my password",
        language_match="native",
        min_words=4,
    )
    assert skip_below_threshold is False
    assert reason == "short_query"


def test_lowercase_acronym_does_not_trigger_abbreviation_branch() -> None:
    # ``api`` is not all-caps — must not block the skip.
    skip, reason = _should_skip_query_rewrite(
        "where can I find the api documentation page",
        language_match="native",
        min_words=4,
    )
    assert skip is True
    assert reason == "eligible_to_skip"


def test_six_letter_uppercase_does_not_trigger_abbreviation_branch() -> None:
    # The regex is bounded to 2–5 chars; a 6-letter shout is just emphasis.
    skip, reason = _should_skip_query_rewrite(
        "PLEASE help me reset the user password right now",
        language_match="native",
        min_words=4,
    )
    assert skip is True
    assert reason == "eligible_to_skip"
