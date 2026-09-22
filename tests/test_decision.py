# ruff: noqa: E402
"""Unit tests for the clarification policy decision engine.

Acceptance criteria covered (spec §Acceptance criteria):
  1. Each block rule → at least one test
  2. clarification_count increments only on Decision.clarify (blocking)
  3. Budget exhaustion: second would-be clarify → answer_with_caveat or escalate
  4. inline_clarify and safety_confirm are not blocked by budget rule
  5. decide() is a pure function — tested in isolation from the pipeline
"""

from __future__ import annotations

import pytest


# Override the conftest autouse fixtures — this module only tests a pure function
# and has no OpenAI / gap-analyzer / language-cache dependencies.
@pytest.fixture(autouse=True)
def mock_openai_client():  # noqa: PT004
    yield


@pytest.fixture(autouse=True)
def clear_detect_language_cache():  # noqa: PT004
    yield


@pytest.fixture(autouse=True)
def reset_gap_analyzer_job_runner_state():  # noqa: PT004
    yield


from backend.chat.decision import (
    MAX_CLARIFICATIONS_PER_SESSION,
    DecisionKind,
    TurnContext,
    decide,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    *,
    active_escalation: bool = False,
    clarification_count: int = 0,
    max_clarifications: int = MAX_CLARIFICATIONS_PER_SESSION,
    guard_failed: bool = False,
    guard_reason: str | None = None,
    explicit_human_request: bool = False,
    faq_direct_hit: bool = False,
    faq_top_score: float | None = None,
    kb_confidence: str = "low",
    kb_has_partial_answer: bool = False,
    kb_contradiction_detected: bool = False,
    low_retrieval_no_chunks: bool = False,
    loop_detected: bool = False,
    loop_overlap_ratio: float | None = None,
    loop_window_size: int = 0,
    loop_docs_repeat: bool = False,
    loop_questions_repeat: bool = False,
    loop_question_similarity: float | None = None,
) -> TurnContext:
    return TurnContext(
        active_escalation=active_escalation,
        clarification_count=clarification_count,
        max_clarifications=max_clarifications,
        guard_failed=guard_failed,
        guard_reason=guard_reason,
        explicit_human_request=explicit_human_request,
        faq_direct_hit=faq_direct_hit,
        faq_top_score=faq_top_score,
        kb_confidence=kb_confidence,
        kb_has_partial_answer=kb_has_partial_answer,
        kb_contradiction_detected=kb_contradiction_detected,
        low_retrieval_no_chunks=low_retrieval_no_chunks,
        loop_detected=loop_detected,
        loop_overlap_ratio=loop_overlap_ratio,
        loop_window_size=loop_window_size,
        loop_docs_repeat=loop_docs_repeat,
        loop_questions_repeat=loop_questions_repeat,
        loop_question_similarity=loop_question_similarity,
    )


# ---------------------------------------------------------------------------
# Block rule 1: Guard failure → reject
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ctx_kwargs",
    [
        pytest.param({"guard_failed": True, "guard_reason": "injection"}, id="alone"),
        pytest.param(
            {"guard_failed": True, "explicit_human_request": True},
            id="beats_human_request",
        ),
    ],
)
def test_guard_failure_returns_reject(ctx_kwargs: dict) -> None:
    """Guard failure is checked first — even explicit human request does not override."""
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == DecisionKind.reject


# ---------------------------------------------------------------------------
# Block rule 2: Explicit human request → escalate(explicit_human_request)
# ---------------------------------------------------------------------------

def test_explicit_human_request_escalates() -> None:
    d = decide(_ctx(explicit_human_request=True))
    assert d.kind == DecisionKind.escalate
    assert d.escalate_reason == "explicit_human_request"


# ---------------------------------------------------------------------------
# Block rule 3: Active escalation → forward_to_active_ticket; no clarify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ctx_kwargs",
    [
        pytest.param({"active_escalation": True}, id="alone"),
        pytest.param(
            {"active_escalation": True, "faq_direct_hit": True}, id="beats_faq_hit"
        ),
    ],
)
def test_active_escalation_forwards_to_ticket(ctx_kwargs: dict) -> None:
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == DecisionKind.forward_to_active_ticket


# ---------------------------------------------------------------------------
# Block rule 4: Clarification budget exhausted → answer_with_caveat or escalate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("ctx_kwargs", "expected_kind", "expected_extra"),
    [
        pytest.param(
            {
                "clarification_count": 1,
                "max_clarifications": 1,
                "kb_confidence": "low",
                "kb_has_partial_answer": True,
                "kb_contradiction_detected": True,  # would otherwise clarify
            },
            DecisionKind.answer_with_caveat,
            {"budget_blocked": True},
            id="partial_answer_returns_caveat",
        ),
        pytest.param(
            {
                "clarification_count": 1,
                "max_clarifications": 1,
                "kb_confidence": "low",
                "kb_has_partial_answer": False,
                "kb_contradiction_detected": True,
            },
            DecisionKind.escalate,
            {"escalate_reason": "clarify_loop_limit", "budget_blocked": True},
            id="no_partial_answer_escalates",
        ),
        pytest.param(
            {
                "clarification_count": 0,
                "max_clarifications": 1,
                "kb_confidence": "low",
                "kb_contradiction_detected": True,
            },
            DecisionKind.clarify,
            {"clarify_type": "blocking"},
            id="not_yet_exhausted_allows_clarify",
        ),
    ],
)
def test_budget_exhaustion(ctx_kwargs: dict, expected_kind, expected_extra: dict) -> None:
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == expected_kind
    for attr, value in expected_extra.items():
        assert getattr(d, attr) == value


# ---------------------------------------------------------------------------
# Block rule 5: FAQ direct hit → answer_from_faq; no clarify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ctx_kwargs",
    [
        pytest.param({"faq_direct_hit": True, "faq_top_score": 0.95}, id="alone"),
        pytest.param(
            {
                "faq_direct_hit": True,
                "faq_top_score": 0.95,
                "clarification_count": 99,
                "max_clarifications": 1,
            },
            id="not_blocked_by_budget",
        ),
    ],
)
def test_faq_direct_hit_returns_answer_from_faq(ctx_kwargs: dict) -> None:
    """FAQ direct hit short-circuits before the budget check — always allowed."""
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == DecisionKind.answer_from_faq


# ---------------------------------------------------------------------------
# Block rule 6: Partial answer + non-critical slot → inline clarify (budget-free)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ctx_kwargs",
    [
        pytest.param({"kb_confidence": "medium", "kb_has_partial_answer": True}, id="plain"),
        pytest.param(
            {
                "kb_confidence": "medium",
                "kb_has_partial_answer": True,
                "clarification_count": 1,
                "max_clarifications": 1,
            },
            id="not_blocked_by_exhausted_budget",
        ),
    ],
)
def test_inline_clarify(ctx_kwargs: dict) -> None:
    """Inline clarify must not be suppressed by the blocking-clarify budget rule."""
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == DecisionKind.answer_with_caveat_and_inline_clarify
    assert d.clarify_type == "inline"
    assert d.budget_blocked is False


# ---------------------------------------------------------------------------
# Counter semantics: trace_dict increments only for blocking clarify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("ctx_kwargs", "expected_after"),
    [
        pytest.param(
            {
                "kb_confidence": "low",
                "kb_contradiction_detected": True,
                "clarification_count": 0,
                "max_clarifications": 1,
            },
            1,
            id="blocking_clarify_increments",
        ),
        pytest.param({"kb_confidence": "high"}, 0, id="non_clarify_does_not_increment"),
        pytest.param(
            {"kb_confidence": "medium", "kb_has_partial_answer": True},
            0,
            id="inline_clarify_does_not_increment",
        ),
    ],
)
def test_trace_dict_counter_semantics(ctx_kwargs: dict, expected_after: int) -> None:
    d = decide(_ctx(**ctx_kwargs))
    td = d.trace_dict(clarification_count_before=0)
    assert td["clarification_count_before"] == 0
    assert td["clarification_count_after"] == expected_after


# ---------------------------------------------------------------------------
# High-confidence KB → answer_with_citations (no clarify, no budget concern)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ctx_kwargs",
    [
        pytest.param({"kb_confidence": "high"}, id="alone"),
        pytest.param(
            {"kb_confidence": "high", "clarification_count": 99, "max_clarifications": 1},
            id="not_affected_by_budget",
        ),
    ],
)
def test_high_kb_confidence_returns_citations(ctx_kwargs: dict) -> None:
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == DecisionKind.answer_with_citations


# ---------------------------------------------------------------------------
# Low confidence: escalate when no allowed reason, clarify when one applies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("ctx_kwargs", "expected_kind", "expected_attr", "expected_value"),
    [
        pytest.param(
            {"kb_confidence": "low", "low_retrieval_no_chunks": True},
            DecisionKind.escalate,
            "escalate_reason",
            "low_confidence_no_path",
            id="no_chunks_escalates",
        ),
        pytest.param(
            {
                "kb_confidence": "low",
                "kb_contradiction_detected": False,
                "low_retrieval_no_chunks": False,
            },
            DecisionKind.clarify,
            "clarify_reason",
            "low_retrieval_confidence",
            id="no_signal_clarifies",
        ),
        pytest.param(
            {
                "kb_confidence": "low",
                "kb_contradiction_detected": True,
                "clarification_count": 0,
                "max_clarifications": 1,
            },
            DecisionKind.clarify,
            "clarify_reason",
            "multiple_conflicting_matches",
            id="contradiction_clarifies",
        ),
    ],
)
def test_low_confidence_routing(
    ctx_kwargs: dict, expected_kind, expected_attr: str, expected_value: str
) -> None:
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == expected_kind
    assert getattr(d, expected_attr) == expected_value


# ---------------------------------------------------------------------------
# Decision.is_blocking_clarify() helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("ctx_kwargs", "expected"),
    [
        pytest.param(
            {
                "kb_confidence": "low",
                "kb_contradiction_detected": True,
                "clarification_count": 0,
                "max_clarifications": 1,
            },
            True,
            id="true_for_clarify_decision",
        ),
        pytest.param(
            {"kb_confidence": "medium", "kb_has_partial_answer": True},
            False,
            id="false_for_inline",
        ),
        pytest.param({"explicit_human_request": True}, False, id="false_for_escalate"),
    ],
)
def test_is_blocking_clarify(ctx_kwargs: dict, expected: bool) -> None:
    d = decide(_ctx(**ctx_kwargs))
    assert d.is_blocking_clarify() is expected


# ---------------------------------------------------------------------------
# Loop detection (block rule 5b)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("ctx_kwargs", "expected_kind", "expected_escalate_reason"),
    [
        pytest.param(
            {
                "kb_confidence": "high",
                "loop_detected": True,
                "loop_overlap_ratio": 0.75,
                "loop_window_size": 3,
            },
            DecisionKind.escalate,
            "loop_detected_repeat_source_docs",
            id="overrides_high_kb_confidence",
        ),
        pytest.param(
            {
                "active_escalation": True,
                "loop_detected": True,
                "loop_overlap_ratio": 1.0,
                "loop_window_size": 3,
            },
            DecisionKind.forward_to_active_ticket,
            None,
            id="does_not_override_active_escalation",
        ),
        pytest.param(
            {
                "faq_direct_hit": True,
                "faq_top_score": 0.95,
                "loop_detected": True,
                "loop_overlap_ratio": 0.8,
                "loop_window_size": 3,
            },
            DecisionKind.answer_from_faq,
            None,
            id="does_not_override_faq_direct_hit",
        ),
        pytest.param(
            {"kb_confidence": "high", "loop_detected": False},
            DecisionKind.answer_with_citations,
            None,
            id="not_detected_falls_through_to_normal_routing",
        ),
    ],
)
def test_loop_detection_precedence(
    ctx_kwargs: dict, expected_kind, expected_escalate_reason: str | None
) -> None:
    """Loop signal must override the high-confidence answer path — the user is
    stuck on one topic and re-answering won't help — but block rules 3 (active
    escalation) and 5 (FAQ direct hit) are still checked first."""
    d = decide(_ctx(**ctx_kwargs))
    assert d.kind == expected_kind
    if expected_escalate_reason is not None:
        assert d.escalate_reason == expected_escalate_reason


def test_trace_dict_carries_loop_fields() -> None:
    turn = _ctx(
        kb_confidence="high",
        loop_detected=True,
        loop_overlap_ratio=0.6,
        loop_window_size=3,
        loop_docs_repeat=True,
        loop_questions_repeat=True,
        loop_question_similarity=0.9,
    )
    d = decide(turn)
    loop_trace = d.loop_trace_dict(turn)
    assert loop_trace["loop_detected"] is True
    assert loop_trace["loop_overlap_ratio"] == 0.6
    assert loop_trace["loop_window_size"] == 3
    assert loop_trace["loop_docs_repeat"] is True
    assert loop_trace["loop_questions_repeat"] is True
    assert loop_trace["loop_question_similarity"] == 0.9


def test_docs_only_repeat_answers_normally_and_is_traceable() -> None:
    """Single-document tenant: docs repeat on every coherent conversation,
    but distinct questions mean no loop — the generated answer is delivered
    and the trace still records the docs-only repeat for monitoring."""
    turn = _ctx(
        kb_confidence="medium",
        kb_has_partial_answer=True,
        loop_detected=False,
        loop_overlap_ratio=1.0,
        loop_window_size=3,
        loop_docs_repeat=True,
        loop_questions_repeat=False,
        loop_question_similarity=0.1,
    )
    d = decide(turn)
    assert d.kind == DecisionKind.answer_with_caveat_and_inline_clarify
    loop_trace = d.loop_trace_dict(turn)
    assert loop_trace["loop_detected"] is False
    assert loop_trace["loop_docs_repeat"] is True
    assert loop_trace["loop_questions_repeat"] is False
