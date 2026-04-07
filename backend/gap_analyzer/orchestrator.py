"""Phase 1 public orchestrator skeleton for Gap Analyzer.

The orchestrator intentionally exposes command signatures only. Business
behavior lands in later phases once the module boundary is in place.
"""

from __future__ import annotations

from uuid import UUID

from backend.gap_analyzer.domain import SignalWeightPolicy
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.repository import GapAnalyzerRepository
from backend.gap_analyzer.schemas import GapRunMode, ModeAResult, ModeBResult, RecalculateCommandResult


class GapAnalyzerOrchestrator:
    """Phase 1 command-routing shape only."""

    def __init__(self, repository: GapAnalyzerRepository | None = None) -> None:
        self.repository = repository

    def ingest_signal(self, signal: GapSignal) -> None:
        repository = self._require_repository()
        repository.store_signal(
            signal,
            signal_weight=self._resolve_signal_weight(signal),
        )

    async def run_mode_a(self, tenant_id: UUID) -> ModeAResult:
        raise NotImplementedError("Mode A pipeline lands in Phase 3")

    async def run_mode_b(self, tenant_id: UUID) -> ModeBResult:
        raise NotImplementedError("Mode B pipeline lands in Phase 4")

    def record_assistant_feedback(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
        feedback_value: str,
    ) -> bool:
        if feedback_value not in {"up", "down", "none"}:
            return False

        repository = self._require_repository()
        signal_state = repository.get_signal_state_for_assistant_message(
            tenant_id=tenant_id,
            assistant_message_id=assistant_message_id,
        )
        if signal_state is None:
            return False

        repository.update_signal_weight(
            gap_question_id=signal_state.gap_question_id,
            signal_weight=self._resolve_signal_weight_from_values(
                answer_confidence=signal_state.answer_confidence,
                had_fallback=signal_state.had_fallback,
                was_rejected=signal_state.had_rejected,
                was_escalated=signal_state.had_escalation,
                user_thumbed_down=feedback_value == "down",
            ),
        )
        return True

    async def request_recalculation(
        self,
        tenant_id: UUID,
        mode: GapRunMode,
    ) -> RecalculateCommandResult:
        raise NotImplementedError("Async recalc orchestration lands in Phase 5")

    def _require_repository(self) -> GapAnalyzerRepository:
        if self.repository is None:
            raise RuntimeError("Gap Analyzer repository is required for command execution")
        return self.repository

    def _resolve_signal_weight(self, signal: GapSignal) -> float:
        return self._resolve_signal_weight_from_values(
            answer_confidence=signal.answer_confidence,
            had_fallback=signal.had_fallback,
            was_rejected=signal.was_rejected,
            was_escalated=signal.was_escalated,
            user_thumbed_down=signal.user_thumbed_down,
        )

    def _resolve_signal_weight_from_values(
        self,
        *,
        answer_confidence: float | None,
        had_fallback: bool,
        was_rejected: bool,
        was_escalated: bool,
        user_thumbed_down: bool,
    ) -> float:
        policy = SignalWeightPolicy()
        weight = policy.normal_weight
        if answer_confidence is not None and answer_confidence < policy.low_conf_threshold:
            weight = max(weight, policy.low_conf_weight)
        if was_rejected or had_fallback:
            weight = max(weight, policy.rejection_weight)
        if was_escalated:
            weight = max(weight, policy.escalation_weight)
        if user_thumbed_down:
            weight = max(weight, policy.thumbdown_weight)
        return weight
