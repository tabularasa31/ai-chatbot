"""Gap Analyzer bounded module exports."""

from __future__ import annotations

from typing import Any

__all__ = ["GapAnalyzerOrchestrator", "GapSignal"]


def __getattr__(name: str) -> Any:
    if name == "GapAnalyzerOrchestrator":
        from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator

        return GapAnalyzerOrchestrator
    if name == "GapSignal":
        from backend.gap_analyzer.events import GapSignal

        return GapSignal
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
