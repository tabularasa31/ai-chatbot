"""Phase 1 policy objects for Gap Analyzer.

These are intentionally data-only in Phase 1. Behavioral methods land in later
phases once the module boundaries and schema are stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CoveragePolicy:
    mode_b_uncovered: float = 0.45
    mode_a_gate: float = 0.45
    covered_threshold: float = 0.70


@dataclass(frozen=True)
class ClusteringPolicy:
    similarity_threshold: float = 0.75
    link_threshold: float = 0.80
    merge_threshold: float = 0.85
    pgvector_link_candidate_limit: int = 10
    full_recluster_interval_days: int = 7
    question_lookback_days: int = 30


@dataclass(frozen=True)
class SignalWeightPolicy:
    thumbdown_weight: float = 4.0
    escalation_weight: float = 3.0
    rejection_weight: float = 2.0
    low_conf_weight: float = 1.5
    normal_weight: float = 1.0
    low_conf_threshold: float = 0.5


@dataclass(frozen=True)
class GapLifecyclePolicy:
    auto_archive_covered: bool = True
    inactive_days: int = 30
    reopen_multiplier: float = 2.0
    retention_months: int = 12
    dismissal_similarity: float = 0.88


@dataclass(frozen=True)
class DraftGenerationPolicy:
    linked_primary_label_source: Literal["mode_b"] = "mode_b"
    append_mode_a_example_questions: bool = True


@dataclass(frozen=True)
class DocumentScopePolicy:
    excluded_mode_a_file_types: tuple[str, ...] = ("swagger",)
    excluded_mode_a_reason: str = "Swagger/OpenAPI documents are analyzed by a separate analyzer."
