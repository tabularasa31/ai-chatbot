"""Unified guard result contract.

Every guard in ``backend/guards/`` (injection structural + semantic, relevance)
returns a :class:`Verdict`. Before this contract each guard spoke its own dialect
— a bool here, a ``(relevant, reason, profile)`` tuple there, a rich dataclass
elsewhere — so wiring a new guard into the chat pipeline meant reading three
different return shapes. A single frozen dataclass makes the contract obvious:

    async def guard(...) -> Verdict

``Verdict`` carries exactly four things:

- ``blocked``  — did this guard divert the turn away from the normal answer path?
- ``reason``   — machine-readable :class:`VerdictReason` (also the routing token).
- ``score``    — confidence 0.0-1.0 (cosine similarity, or 0.0 when N/A).
- ``evidence`` — what triggered it (matched pattern / short note), for debugging.

``blocked`` is *derived* from ``reason`` via :meth:`Verdict.of` so the two can
never disagree. The ``reason`` enum values are byte-for-byte the string tokens
the chat pipeline already routes on (``"offtopic"``, ``"support_complaint"`` …),
so ``verdict.reason.value`` is a drop-in for the legacy ``reason`` strings.

To add a new guard: pick (or add) a :class:`VerdictReason`, decide whether it is
blocking (add it to ``_BLOCKING`` if so), and return ``Verdict.of(reason, ...)``.
See ``AGENTS.md → "Guards subsystem"`` for the full walkthrough.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerdictReason(str, Enum):
    """Why a guard reached its verdict.

    Values double as the machine-readable routing token consumed by the chat
    pipeline, so they must stay stable (they are compared against the legacy
    ``CATEGORY_*`` constants and persisted in ``guard_events.reason``).
    """

    # Injection detector
    OK = "ok"  # nothing detected (pass-through)
    INJECTION_STRUCTURAL = "injection_structural"  # level 1, regex/leet match
    INJECTION_SEMANTIC = "injection_semantic"  # level 2, embedding similarity

    # Relevance guard — LLM classifier categories
    RELEVANT = "relevant"
    OFFTOPIC = "offtopic"
    SUPPORT_COMPLAINT = "support_complaint"
    SOCIAL = "social"
    SOCIAL_QUESTION = "social_question"

    # Relevance guard — fail-open / bypass reasons. The guard did NOT render a
    # blocking judgment; the turn proceeds as relevant. Kept distinct so callers
    # can tell a real "relevant" verdict from a degraded pass-through (used by
    # the retrieval escalation gate, which must not arm a handoff on a guess).
    NO_PROFILE = "no_profile"
    SHORT_QUERY_BYPASS = "short_query_bypass"
    CIRCUIT_OPEN = "circuit_open"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


# Reasons that divert the turn off the normal answer path. Everything else is a
# pass-through (``blocked=False``). Injection detections and the four
# non-relevant relevance categories block; OK / RELEVANT and every fail-open
# reason do not.
_BLOCKING: frozenset[VerdictReason] = frozenset(
    {
        VerdictReason.INJECTION_STRUCTURAL,
        VerdictReason.INJECTION_SEMANTIC,
        VerdictReason.OFFTOPIC,
        VerdictReason.SUPPORT_COMPLAINT,
        VerdictReason.SOCIAL,
        VerdictReason.SOCIAL_QUESTION,
    }
)

# Fail-open / bypass reasons: the guard could not (or chose not to) render a
# real judgment and passed the turn through. Callers that gate a side effect on
# a *trusted* verdict (e.g. arming an escalation handoff) must exclude these.
FAIL_OPEN_REASONS: frozenset[VerdictReason] = frozenset(
    {
        VerdictReason.NO_PROFILE,
        VerdictReason.SHORT_QUERY_BYPASS,
        VerdictReason.CIRCUIT_OPEN,
        VerdictReason.TIMEOUT,
        VerdictReason.ERROR,
        VerdictReason.CANCELLED,
    }
)


@dataclass(frozen=True)
class Verdict:
    """A single guard's decision. See module docstring for the contract."""

    blocked: bool
    reason: VerdictReason
    score: float = 0.0
    evidence: str | None = None

    @classmethod
    def of(
        cls,
        reason: VerdictReason,
        *,
        score: float = 0.0,
        evidence: str | None = None,
    ) -> Verdict:
        """Build a verdict, deriving ``blocked`` from ``reason``.

        The single construction path guarantees ``blocked`` and ``reason`` can
        never drift apart.
        """
        return cls(
            blocked=reason in _BLOCKING,
            reason=reason,
            score=score,
            evidence=evidence,
        )
