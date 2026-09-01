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
from typing import TYPE_CHECKING, Literal

from backend.core.config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.chat.types import RetrievalContext

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


# Retrieval-confidence thresholds for the three-tier KbConfidence below. They
# live here, next to decide(), because two callers need the same tiering: the
# RAG handler (post-generation, authoritative decision) and the generation step
# (pre-generation, to know whether this turn must be a clarifying question).
KB_HIGH_CONFIDENCE_THRESHOLD = 0.45
KB_LOW_CONFIDENCE_THRESHOLD = 0.4

_KB_CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def floor_kb_confidence(raw: KbConfidence, ceiling: KbConfidence) -> KbConfidence:
    """Return the lower of two KbConfidence tiers."""
    if _KB_CONFIDENCE_RANK[ceiling] < _KB_CONFIDENCE_RANK[raw]:
        return ceiling
    return raw


def classify_kb_confidence(retrieval: RetrievalContext | None) -> KbConfidence:
    """Map retrieval confidence score to the three-tier KbConfidence used by decide().

    When `retrieval.reliability.cap` is set (contradiction cap → ``low``,
    source_overlap cap → ``medium``), the raw similarity-based tier is floored
    by that cap so the cap actually reaches the decision engine. Without this
    floor, caps live only in observability. We deliberately do NOT floor by
    `reliability.score` because the reliability score uses stricter base
    thresholds (`high` only at top score ≥0.8) than this classifier
    (`high` at ≥0.45); flooring by score would silently downgrade many
    uncapped high-confidence queries to medium.
    """
    if retrieval is None or retrieval.best_confidence_score is None:
        return "low"
    score = retrieval.best_confidence_score
    if score >= KB_HIGH_CONFIDENCE_THRESHOLD:
        raw: KbConfidence = "high"
    elif score >= KB_LOW_CONFIDENCE_THRESHOLD:
        raw = "medium"
    else:
        raw = "low"
    cap = retrieval.reliability.cap
    if cap is None:
        return raw
    return floor_kb_confidence(raw, cap)


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

    # Loop-detection signals, computed upstream from chat.messages by
    # _compute_loop_signal. loop_detected is True only when BOTH hold:
    #   - the last N assistant turns drew on the same set of source documents
    #     (docs Jaccard overlap >= threshold), AND
    #   - the current user question repeats a recent prior question
    #     (length-weighted token Jaccard similarity >= threshold).
    # Document overlap alone is not a loop: a tenant whose whole KB is one
    # document produces overlap=1.0 on every coherent conversation. The
    # component signals are carried separately for trace observability.
    loop_detected: bool = False
    loop_overlap_ratio: float | None = None
    loop_window_size: int = 0
    loop_docs_repeat: bool = False
    loop_questions_repeat: bool = False
    loop_question_similarity: float | None = None


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

    def trace_dict(
        self,
        clarification_count_before: int,
        *,
        clarification_charged: bool | None = None,
    ) -> dict:
        """Structured trace fields for this decision (spec §Trace fields).

        ``clarification_charged`` reports whether the caller actually spent a
        clarification on this turn. A blocking clarify the model answered
        instead of asking costs nothing, so the trace must not show the budget
        moving. Defaults to the decision's own classification for callers that
        do not track the reply shape.
        """
        charged = (
            self.is_blocking_clarify()
            if clarification_charged is None
            else clarification_charged
        )
        count_after = (
            clarification_count_before + 1 if charged else clarification_count_before
        )
        return {
            "decision": self.kind.value,
            "decision_reason": self.clarify_reason or self.escalate_reason or "n/a",
            "clarify_type": self.clarify_type,
            "clarification_count_before": clarification_count_before,
            "clarification_count_after": count_after,
            "clarification_charged": charged,
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
            # Component signals: a docs-only repeat (loop_docs_repeat=True,
            # loop_questions_repeat=False) is the single-document-tenant case
            # where the generated answer is delivered instead of escalating.
            "loop_docs_repeat": turn.loop_docs_repeat,
            "loop_questions_repeat": turn.loop_questions_repeat,
            "loop_question_similarity": turn.loop_question_similarity,
        }


def clarify_reason_for(
    *,
    kb_confidence: KbConfidence,
    low_retrieval_no_chunks: bool,
    kb_contradiction_detected: bool,
) -> ClarifyReason | None:
    """Return the first applicable allowed clarify reason, or None.

    v1 sources (no intent classifier):
      - multiple_conflicting_matches: kb_contradiction_detected, populated from
        retrieval.reliability.cap_reason == "contradiction" upstream
      - low_retrieval_confidence: confidence is LOW and we have some chunks
        (zero-chunk case escalates directly as low_confidence_no_path)
    """
    if kb_contradiction_detected:
        return "multiple_conflicting_matches"
    if kb_confidence == "low" and not low_retrieval_no_chunks:
        return "low_retrieval_confidence"
    return None


def _allowed_clarify_reason(turn: TurnContext) -> ClarifyReason | None:
    """TurnContext-shaped wrapper around :func:`clarify_reason_for`."""
    return clarify_reason_for(
        kb_confidence=turn.kb_confidence,
        low_retrieval_no_chunks=turn.low_retrieval_no_chunks,
        kb_contradiction_detected=turn.kb_contradiction_detected,
    )


def requires_blocking_clarify(
    *,
    retrieval: RetrievalContext | None,
    clarification_budget_available: bool,
) -> ClarifyReason | None:
    """Pre-generation twin of the blocking-clarify branch of :func:`decide`.

    ``decide()`` runs *after* generation, so its clarify verdict could only ever
    describe the turn — it could not shape it, and the model answered instead of
    asking often enough to make the verdict cosmetic. The generation step calls
    this before building the prompt and, when a reason comes back, instructs the
    model that this turn must be exactly one clarifying question.

    Only the retrieval-derived signals are available this early. The session
    branches decide() checks first (guard reject, explicit human request, closed
    session, active escalation, FAQ direct hit) all short-circuit before the RAG
    generation step runs, and loop detection only ever produces an escalate — so
    none of them can turn a clarify into something else behind our back.
    """
    if not clarification_budget_available:
        return None
    if retrieval is None:
        return None
    return clarify_reason_for(
        kb_confidence=classify_kb_confidence(retrieval),
        low_retrieval_no_chunks=not retrieval.chunk_texts,
        kb_contradiction_detected=retrieval.reliability.cap_reason == "contradiction",
    )


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
    # same source documents AND the user is repeating the same question, so
    # re-answering won't help. Force escalation through the existing
    # pre-confirm flow rather than emit yet another rephrased answer.
    # (Docs overlap alone never sets loop_detected — see TurnContext.)
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
