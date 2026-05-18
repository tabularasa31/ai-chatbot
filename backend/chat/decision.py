"""Clarification policy decision engine.

Single authoritative function that determines the turn outcome from a
`TurnContext`. All other modules (chat router, escalation service,
observability) must read the resulting `Decision` and act on it — they
must not produce their own competing clarify / answer / escalate decisions.

Block rules (evaluated in order, first match wins):
  1. Guard failure           → reject
  2. Explicit human request  → escalate(explicit_human_request)
  3. Closed session          → acknowledge_closed_or_start_new
  4. Active escalation       → forward_to_active_ticket
  5. Budget exhausted        → answer_with_caveat or escalate(clarify_loop_limit)
     (only matters when the turn would otherwise produce clarify)
  6. FAQ direct hit          → answer_from_faq
  7. Partial answer possible → answer_with_caveat_and_inline_clarify (free, no budget)

v1 limitations (intentional, documented):
  - No intent classifier: ambiguous_intent and missing_critical_slot reasons
    are never emitted. Extend decide() when a classifier is added.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from backend.core.config import settings

# Named constant — never a magic number at call sites.
MAX_CLARIFICATIONS_PER_SESSION: int = settings.clarification_turn_limit

ClarifyReason = Literal[
    "ambiguous_intent",
    "missing_critical_slot",
    "multiple_conflicting_matches",
    "low_retrieval_confidence",
    "unsafe_to_answer_directly",
]

ClarifyType = Literal["blocking", "inline", "safety_confirm", "n/a"]

EscalateReason = Literal[
    "explicit_human_request",
    "clarify_loop_limit",
    "low_confidence_no_path",
    "loop_detected_repeat_source_docs",
    "guard_reject",
    "unknown",
]

KbConfidence = Literal["high", "medium", "low"]


class DecisionKind(str, Enum):
    answer_from_faq = "answer_from_faq"
    answer_with_citations = "answer_with_citations"
    answer_with_caveat = "answer_with_caveat"
    answer_with_caveat_and_inline_clarify = "answer_with_caveat_and_inline_clarify"
    clarify = "clarify"
    diagnose = "diagnose"
    escalate = "escalate"
    reject = "reject"
    acknowledge_closed_or_start_new = "acknowledge_closed_or_start_new"
    forward_to_active_ticket = "forward_to_active_ticket"


@dataclass(frozen=True)
class TurnContext:
    """All signals needed to make a single turn decision.

    Populated in process_chat_message after the RAG pipeline runs, then
    passed to decide(). Fields are read-only; build a new instance per turn.
    """

    # Session state
    session_closed: bool
    active_escalation: bool
    clarification_count: int
    max_clarifications: int

    # Guard signals (from injection / relevance guards)
    guard_failed: bool
    guard_reason: str | None = None

    # Input signals
    explicit_human_request: bool = False

    # FAQ signals
    faq_direct_hit: bool = False
    faq_top_score: float | None = None

    # KB / retrieval signals
    kb_confidence: KbConfidence = "low"
    kb_has_partial_answer: bool = False
    kb_contradiction_detected: bool = False

    # True when retrieval returned zero chunks (guides escalation over clarify)
    low_retrieval_no_chunks: bool = False

    # Loop-detection signals: True when the last N assistant turns drew on
    # the same set of source documents (Jaccard overlap >= configured
    # threshold), suggesting the user is stuck and KB cannot help further.
    # Computed upstream from chat.messages by _compute_loop_signal.
    loop_detected: bool = False
    loop_overlap_ratio: float | None = None
    loop_window_size: int = 0


@dataclass(frozen=True)
class Decision:
    """Turn decision returned by decide().

    Read by process_chat_message and the trace layer; no other module
    should infer its own clarify/answer/escalate outcome.
    """

    kind: DecisionKind
    clarify_reason: ClarifyReason | None = None
    clarify_type: ClarifyType = "n/a"
    escalate_reason: EscalateReason | None = None
    # True when a clarify was suppressed because the budget was exhausted.
    budget_blocked: bool = False
    slot_asked: str | None = None

    def is_blocking_clarify(self) -> bool:
        return self.kind == DecisionKind.clarify and self.clarify_type == "blocking"

    def trace_dict(self, clarification_count_before: int) -> dict:
        """Structured trace fields for this decision (spec §Trace fields)."""
        count_after = (
            clarification_count_before + 1
            if self.is_blocking_clarify()
            else clarification_count_before
        )
        return {
            "decision": self.kind.value,
            "decision_reason": self.clarify_reason or self.escalate_reason or "n/a",
            "clarify_type": self.clarify_type,
            "clarification_count_before": clarification_count_before,
            "clarification_count_after": count_after,
            "budget_blocked": self.budget_blocked,
            "slot_asked": self.slot_asked,
            "escalation_reason": self.escalate_reason,
        }

    def loop_trace_dict(self, turn: TurnContext) -> dict:
        """Loop-detection signals for the trace, independent of the decision kind.

        Always emitted so dashboards can distinguish "loop never evaluated"
        from "loop evaluated and was false" via loop_window_size > 0.
        """
        return {
            "loop_detected": turn.loop_detected,
            "loop_overlap_ratio": turn.loop_overlap_ratio,
            "loop_window_size": turn.loop_window_size,
        }


def _allowed_clarify_reason(turn: TurnContext) -> ClarifyReason | None:
    """Return the first applicable allowed clarify reason, or None.

    v1 sources (no intent classifier):
      - multiple_conflicting_matches: kb_contradiction_detected, populated from
        retrieval.reliability.cap_reason == "contradiction" upstream
      - low_retrieval_confidence: confidence is LOW and we have some chunks
        (zero-chunk case escalates directly as low_confidence_no_path)
    """
    if turn.kb_contradiction_detected:
        return "multiple_conflicting_matches"
    if turn.kb_confidence == "low" and not turn.low_retrieval_no_chunks:
        return "low_retrieval_confidence"
    return None


def decide(turn: TurnContext) -> Decision:
    """Return the authoritative Decision for this chat turn.

    Block rules are evaluated in the order specified by the clarification
    policy spec. The first matching rule wins.
    """
    # Block rule 1: Guard failure
    if turn.guard_failed:
        return Decision(kind=DecisionKind.reject, escalate_reason="guard_reject")

    # Block rule 2: Explicit human request
    if turn.explicit_human_request:
        return Decision(kind=DecisionKind.escalate, escalate_reason="explicit_human_request")

    # Block rule 3: Closed session
    if turn.session_closed:
        return Decision(kind=DecisionKind.acknowledge_closed_or_start_new)

    # Block rule 4: Active escalation
    if turn.active_escalation:
        return Decision(kind=DecisionKind.forward_to_active_ticket)

    # Block rule 6: FAQ direct hit (checked before budget — FAQ never clarifies)
    if turn.faq_direct_hit:
        return Decision(kind=DecisionKind.answer_from_faq)

    # Block rule 6b: Loop detected — the last N assistant turns drew on the
    # same source documents, so re-answering won't help. Force escalation
    # through the existing pre-confirm flow rather than emit yet another
    # rephrased answer.
    if turn.loop_detected:
        return Decision(
            kind=DecisionKind.escalate,
            escalate_reason="loop_detected_repeat_source_docs",
        )

    # KB / retrieval routing
    if turn.kb_confidence == "high":
        return Decision(kind=DecisionKind.answer_with_citations)

    if turn.kb_confidence == "medium":
        if turn.kb_has_partial_answer:
            # Block rule 7: partial answer possible → inline clarify (free, no budget)
            return Decision(
                kind=DecisionKind.answer_with_caveat_and_inline_clarify,
                clarify_type="inline",
            )
        return Decision(kind=DecisionKind.answer_with_caveat)

    # Low confidence path
    reason = _allowed_clarify_reason(turn)
    if reason is not None:
        # Block rule 5: budget exhausted — fall through instead of clarifying
        if turn.clarification_count >= turn.max_clarifications:
            if turn.kb_has_partial_answer:
                return Decision(
                    kind=DecisionKind.answer_with_caveat,
                    budget_blocked=True,
                )
            return Decision(
                kind=DecisionKind.escalate,
                escalate_reason="clarify_loop_limit",
                budget_blocked=True,
            )
        return Decision(
            kind=DecisionKind.clarify,
            clarify_reason=reason,
            clarify_type="blocking",
        )

    # No allowed clarify reason and confidence is low: escalate
    return Decision(kind=DecisionKind.escalate, escalate_reason="low_confidence_no_path")
