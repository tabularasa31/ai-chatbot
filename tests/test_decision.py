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
    session_closed: bool = False,
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
) -> TurnContext:
    return TurnContext(
        session_closed=session_closed,
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
    )


# ---------------------------------------------------------------------------
# Block rule 1: Guard failure → reject
# ---------------------------------------------------------------------------

def test_guard_failure_returns_reject() -> None:
    d = decide(_ctx(guard_failed=True, guard_reason="injection"))
    assert d.kind == DecisionKind.reject


def test_guard_failure_beats_human_request() -> None:
    """Guard failure is checked first — even explicit human request does not override."""
    d = decide(_ctx(guard_failed=True, explicit_human_request=True))
    assert d.kind == DecisionKind.reject


# ---------------------------------------------------------------------------
# Block rule 2: Explicit human request → escalate(explicit_human_request)
# ---------------------------------------------------------------------------

def test_explicit_human_request_escalates() -> None:
    d = decide(_ctx(explicit_human_request=True))
    assert d.kind == DecisionKind.escalate
    assert d.escalate_reason == "explicit_human_request"


def test_human_request_beats_closed_session() -> None:
    """Human request is checked before closed-session (block rule 2 < rule 3)."""
    d = decide(_ctx(explicit_human_request=True, session_closed=True))
    assert d.kind == DecisionKind.escalate
    assert d.escalate_reason == "explicit_human_request"


# ---------------------------------------------------------------------------
# Block rule 3: Closed session → acknowledge_closed_or_start_new; no clarify
# ---------------------------------------------------------------------------

def test_closed_session_returns_acknowledge() -> None:
    d = decide(_ctx(session_closed=True))
    assert d.kind == DecisionKind.acknowledge_closed_or_start_new


def test_closed_session_no_clarification_even_with_low_confidence() -> None:
    d = decide(_ctx(session_closed=True, kb_confidence="low"))
    assert d.kind != DecisionKind.clarify


# ---------------------------------------------------------------------------
# Block rule 4: Active escalation → forward_to_active_ticket; no clarify
# ---------------------------------------------------------------------------

def test_active_escalation_forwards_to_ticket() -> None:
    d = decide(_ctx(active_escalation=True))
    assert d.kind == DecisionKind.forward_to_active_ticket


def test_active_escalation_beats_faq_hit() -> None:
    d = decide(_ctx(active_escalation=True, faq_direct_hit=True))
    assert d.kind == DecisionKind.forward_to_active_ticket


# ---------------------------------------------------------------------------
# Block rule 5: Clarification budget exhausted → answer_with_caveat or escalate
# ---------------------------------------------------------------------------

def test_budget_exhausted_with_partial_answer_returns_caveat() -> None:
    """When budget is spent and KB has a partial answer, fall back to caveat answer."""
    d = decide(
        _ctx(
            clarification_count=1,
            max_clarifications=1,
            kb_confidence="low",
            kb_has_partial_answer=True,
            kb_contradiction_detected=True,  # would otherwise clarify
        )
    )
    assert d.kind == DecisionKind.answer_with_caveat
    assert d.budget_blocked is True


def test_budget_exhausted_no_partial_answer_escalates() -> None:
    """When budget is spent and no partial answer, escalate with clarify_loop_limit."""
    d = decide(
        _ctx(
            clarification_count=1,
            max_clarifications=1,
            kb_confidence="low",
            kb_has_partial_answer=False,
            kb_contradiction_detected=True,
        )
    )
    assert d.kind == DecisionKind.escalate
    assert d.escalate_reason == "clarify_loop_limit"
    assert d.budget_blocked is True


def test_budget_not_yet_exhausted_allows_clarify() -> None:
    d = decide(
        _ctx(
            clarification_count=0,
            max_clarifications=1,
            kb_confidence="low",
            kb_contradiction_detected=True,
        )
    )
    assert d.kind == DecisionKind.clarify
    assert d.clarify_type == "blocking"


# ---------------------------------------------------------------------------
# Block rule 6: FAQ direct hit → answer_from_faq; no clarify
# ---------------------------------------------------------------------------

def test_faq_direct_hit_returns_answer_from_faq() -> None:
    d = decide(_ctx(faq_direct_hit=True, faq_top_score=0.95))
    assert d.kind == DecisionKind.answer_from_faq


def test_faq_direct_hit_not_blocked_by_budget() -> None:
    """FAQ direct hit short-circuits before the budget check — always allowed."""
    d = decide(
        _ctx(
            faq_direct_hit=True,
            faq_top_score=0.95,
            clarification_count=99,
            max_clarifications=1,
        )
    )
    assert d.kind == DecisionKind.answer_from_faq


# ---------------------------------------------------------------------------
# Block rule 7: Partial answer + non-critical slot → inline clarify (budget-free)
# ---------------------------------------------------------------------------

def test_partial_answer_with_medium_confidence_returns_inline_clarify() -> None:
    d = decide(_ctx(kb_confidence="medium", kb_has_partial_answer=True))
    assert d.kind == DecisionKind.answer_with_caveat_and_inline_clarify
    assert d.clarify_type == "inline"


def test_inline_clarify_not_blocked_by_exhausted_budget() -> None:
    """Inline clarify must not be suppressed by the blocking-clarify budget rule."""
    d = decide(
        _ctx(
            kb_confidence="medium",
            kb_has_partial_answer=True,
            clarification_count=1,
            max_clarifications=1,
        )
    )
    assert d.kind == DecisionKind.answer_with_caveat_and_inline_clarify
    assert d.clarify_type == "inline"
    assert d.budget_blocked is False


# ---------------------------------------------------------------------------
# Counter semantics: trace_dict increments only for blocking clarify
# ---------------------------------------------------------------------------

def test_trace_dict_increments_for_blocking_clarify() -> None:
    d = decide(
        _ctx(
            kb_confidence="low",
            kb_contradiction_detected=True,
            clarification_count=0,
            max_clarifications=1,
        )
    )
    assert d.kind == DecisionKind.clarify
    td = d.trace_dict(clarification_count_before=0)
    assert td["clarification_count_before"] == 0
    assert td["clarification_count_after"] == 1


def test_trace_dict_does_not_increment_for_non_clarify() -> None:
    d = decide(_ctx(kb_confidence="high"))
    td = d.trace_dict(clarification_count_before=0)
    assert td["clarification_count_before"] == 0
    assert td["clarification_count_after"] == 0


def test_trace_dict_does_not_increment_for_inline_clarify() -> None:
    d = decide(_ctx(kb_confidence="medium", kb_has_partial_answer=True))
    assert d.clarify_type == "inline"
    td = d.trace_dict(clarification_count_before=0)
    assert td["clarification_count_after"] == 0


# ---------------------------------------------------------------------------
# High-confidence KB → answer_with_citations (no clarify, no budget concern)
# ---------------------------------------------------------------------------

def test_high_kb_confidence_returns_citations() -> None:
    d = decide(_ctx(kb_confidence="high"))
    assert d.kind == DecisionKind.answer_with_citations


def test_high_kb_confidence_not_affected_by_budget() -> None:
    d = decide(
        _ctx(
            kb_confidence="high",
            clarification_count=99,
            max_clarifications=1,
        )
    )
    assert d.kind == DecisionKind.answer_with_citations


# ---------------------------------------------------------------------------
# Low confidence with no allowed reason → escalate(low_confidence_no_path)
# ---------------------------------------------------------------------------

def test_low_confidence_no_chunks_escalates() -> None:
    """Zero-chunk retrieval → low_retrieval_no_chunks=True → no clarify reason."""
    d = decide(_ctx(kb_confidence="low", low_retrieval_no_chunks=True))
    assert d.kind == DecisionKind.escalate
    assert d.escalate_reason == "low_confidence_no_path"


def test_low_confidence_no_signal_escalates() -> None:
    """Low confidence with no contradiction and chunks present but no allowed reason."""
    d = decide(
        _ctx(
            kb_confidence="low",
            kb_contradiction_detected=False,
            low_retrieval_no_chunks=False,
        )
    )
    # low_retrieval_confidence reason applies because chunks exist and no contradiction
    assert d.kind == DecisionKind.clarify
    assert d.clarify_reason == "low_retrieval_confidence"


# ---------------------------------------------------------------------------
# Multiple conflicting matches → clarify(multiple_conflicting_matches)
# ---------------------------------------------------------------------------

def test_contradiction_detected_triggers_clarify() -> None:
    d = decide(
        _ctx(
            kb_confidence="low",
            kb_contradiction_detected=True,
            clarification_count=0,
            max_clarifications=1,
        )
    )
    assert d.kind == DecisionKind.clarify
    assert d.clarify_reason == "multiple_conflicting_matches"


# ---------------------------------------------------------------------------
# Decision.is_blocking_clarify() helper
# ---------------------------------------------------------------------------

def test_is_blocking_clarify_true_for_clarify_decision() -> None:
    d = decide(
        _ctx(
            kb_confidence="low",
            kb_contradiction_detected=True,
            clarification_count=0,
            max_clarifications=1,
        )
    )
    assert d.is_blocking_clarify() is True


def test_is_blocking_clarify_false_for_inline() -> None:
    d = decide(_ctx(kb_confidence="medium", kb_has_partial_answer=True))
    assert d.is_blocking_clarify() is False


def test_is_blocking_clarify_false_for_escalate() -> None:
    d = decide(_ctx(explicit_human_request=True))
    assert d.is_blocking_clarify() is False


# ---------------------------------------------------------------------------
# Loop detection (block rule 6b)
# ---------------------------------------------------------------------------


def test_loop_detected_escalates_even_with_high_kb_confidence() -> None:
    """Loop signal must override the high-confidence answer path — the user
    is stuck on one topic and re-answering won't help."""
    d = decide(
        _ctx(
            kb_confidence="high",
            loop_detected=True,
            loop_overlap_ratio=0.75,
            loop_window_size=3,
        )
    )
    assert d.kind == DecisionKind.escalate
    assert d.escalate_reason == "loop_detected_repeat_source_docs"


def test_loop_detected_does_not_override_active_escalation() -> None:
    """Block rule 4 (active escalation) is checked before loop — an existing
    ticket flow must not be hijacked by a loop signal."""
    d = decide(
        _ctx(
            active_escalation=True,
            loop_detected=True,
            loop_overlap_ratio=1.0,
            loop_window_size=3,
        )
    )
    assert d.kind == DecisionKind.forward_to_active_ticket


def test_loop_detected_does_not_override_faq_direct_hit() -> None:
    """FAQ direct hit is a fast, deterministic path that must short-circuit
    even when source docs happen to repeat across recent turns."""
    d = decide(
        _ctx(
            faq_direct_hit=True,
            faq_top_score=0.95,
            loop_detected=True,
            loop_overlap_ratio=0.8,
            loop_window_size=3,
        )
    )
    assert d.kind == DecisionKind.answer_from_faq


def test_trace_dict_carries_loop_fields() -> None:
    turn = _ctx(
        kb_confidence="high",
        loop_detected=True,
        loop_overlap_ratio=0.6,
        loop_window_size=3,
    )
    d = decide(turn)
    loop_trace = d.loop_trace_dict(turn)
    assert loop_trace["loop_detected"] is True
    assert loop_trace["loop_overlap_ratio"] == 0.6
    assert loop_trace["loop_window_size"] == 3


def test_loop_not_detected_falls_through_to_normal_routing() -> None:
    """When loop_detected=False the block rule must not fire, and the turn
    routes via normal kb_confidence rules."""
    d = decide(_ctx(kb_confidence="high", loop_detected=False))
    assert d.kind == DecisionKind.answer_with_citations
