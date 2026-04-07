"""Phase 1 public orchestrator skeleton for Gap Analyzer.

The orchestrator intentionally exposes command signatures only. Business
behavior lands in later phases once the module boundary is in place.
"""

from __future__ import annotations

from uuid import UUID

from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.repository import GapAnalyzerRepository
from backend.gap_analyzer.schemas import GapRunMode, ModeAResult, ModeBResult, RecalculateCommandResult


class GapAnalyzerOrchestrator:
    """Phase 1 command-routing shape only."""

    def __init__(self, repository: GapAnalyzerRepository | None = None) -> None:
        self.repository = repository

    async def ingest_signal(self, signal: GapSignal) -> None:
        raise NotImplementedError("Gap Analyzer ingestion lands in Phase 2")

    async def run_mode_a(self, tenant_id: UUID) -> ModeAResult:
        raise NotImplementedError("Mode A pipeline lands in Phase 3")

    async def run_mode_b(self, tenant_id: UUID) -> ModeBResult:
        raise NotImplementedError("Mode B pipeline lands in Phase 4")

    async def request_recalculation(
        self,
        tenant_id: UUID,
        mode: GapRunMode,
    ) -> RecalculateCommandResult:
        raise NotImplementedError("Async recalc orchestration lands in Phase 5")
