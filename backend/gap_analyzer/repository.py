"""Phase 1 persistence seams for Gap Analyzer.

This file intentionally defines repository interfaces only. Query/read semantics
and concrete implementations land in later phases.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.schemas import GapRunMode


class GapAnalyzerRepository(Protocol):
    """Phase 1 command-side persistence boundary."""

    def store_signal(self, signal: GapSignal) -> None:
        ...

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> None:
        ...
