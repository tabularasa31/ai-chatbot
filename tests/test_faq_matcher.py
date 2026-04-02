from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest

from backend.faq.faq_matcher import FAQRow, match_faq


def _fake_rows(*rows: FAQRow) -> list[FAQRow]:
    return list(rows)


def test_faq_direct_hit_guard_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.99,
    )

    other = FAQRow(
        id=uuid.uuid4(),
        question="Refund policy",
        answer="Refunds are possible within 30 days.",
        approved=True,
        score=0.88,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top, other),
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: True,
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="How do I reset my password?",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.strategy == "faq_direct"
    assert result.faq_items == [top]
    assert result.selected_score == top.score
    assert result.direct_guard_used is True
    assert result.direct_guard_passed is True
    assert result.selected_faq_id == str(top.id)


def test_faq_direct_not_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=False,
        score=0.99,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top),
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: (_ for _ in ()).throw(AssertionError("guard must not run")),
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="How do I reset my password?",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.strategy == "faq_context"
    assert result.faq_items == [top]
    assert result.selected_score == top.score
    assert result.direct_guard_used is False
    assert result.direct_guard_passed is False
    assert result.decision_reason == "high_score_not_approved"


def test_faq_direct_guard_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.99,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top),
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: False,
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="How do I reset my password?",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.strategy == "faq_context"
    assert result.faq_items == [top]
    assert result.selected_score == top.score
    assert result.direct_guard_used is True
    assert result.direct_guard_passed is False
    assert result.decision_reason == "high_score_guard_failed_or_error"


def test_faq_context_adds_top_n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")
    monkeypatch.setenv("FAQ_CONTEXT_MAX_ITEMS", "2")

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.8,
    )
    second = FAQRow(
        id=uuid.uuid4(),
        question="Change email",
        answer="Go to settings.",
        approved=True,
        score=0.78,
    )
    third = FAQRow(
        id=uuid.uuid4(),
        question="Delete account",
        answer="Go to privacy settings.",
        approved=True,
        score=0.76,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top, second, third),
    )

    # Guard not expected in this score band.
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: (_ for _ in ()).throw(AssertionError("guard must not run")),
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="reset password",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.strategy == "faq_context"
    assert len(result.faq_items) == 2
    assert result.faq_items[0] == top
    assert result.faq_items[1] == second


def test_faq_ignored_below_context_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")

    top = FAQRow(
        id=uuid.uuid4(),
        question="Something",
        answer="Nothing",
        approved=True,
        score=0.74,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top),
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="Something else",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.strategy == "rag_only"
    assert result.faq_items == []
    assert result.decision_reason == "score_below_context_threshold"


def test_match_result_contains_decision_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.99,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top),
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: True,
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="How do I reset my password?",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.decision_reason
    assert result.direct_guard_used is True
    assert result.direct_guard_passed is True


def test_approved_candidate_can_be_promoted_for_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAQ_DIRECT_THRESHOLD", "0.92")
    monkeypatch.setenv("FAQ_CONTEXT_THRESHOLD", "0.75")
    monkeypatch.setenv("FAQ_APPROVED_PROMOTION_DELTA", "0.02")

    top_unapproved = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password quickly?",
        answer="Unapproved answer.",
        approved=False,
        score=0.95,
    )
    second_approved = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.94,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: _fake_rows(top_unapproved, second_approved),
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: True,
    )

    result = match_faq(
        client_id=uuid.uuid4(),
        question="How can I reset password?",
        question_embedding=[0.1] * 1536,
        db=Mock(),
    )

    assert result.strategy == "faq_direct"
    assert result.selected_faq_id == str(second_approved.id)
    assert result.selected_score == second_approved.score
    assert result.faq_items == [second_approved]
    assert result.decision_reason == "approved_promoted_high_score_guard_passed"
