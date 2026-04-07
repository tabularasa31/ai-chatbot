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
        user_thumbed_down: bool,
    ) -> bool:
        if not user_thumbed_down:
            return False

        repository = self._require_repository()
        policy = SignalWeightPolicy()
        return repository.reweight_signal_for_assistant_message(
            tenant_id=tenant_id,
            assistant_message_id=assistant_message_id,
            signal_weight=policy.thumbdown_weight,
        )

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
        policy = SignalWeightPolicy()
        weight = policy.normal_weight
        if signal.answer_confidence is not None and signal.answer_confidence < policy.low_conf_threshold:
            weight = max(weight, policy.low_conf_weight)
        if signal.was_rejected or signal.had_fallback:
            weight = max(weight, policy.rejection_weight)
        if signal.was_escalated:
            weight = max(weight, policy.escalation_weight)
        if signal.user_thumbed_down:
            weight = max(weight, policy.thumbdown_weight)
        return weight
