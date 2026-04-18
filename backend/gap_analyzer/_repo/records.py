"""Public dataclass records for Gap Analyzer repository layer.

These are stdlib-only so any submodule can import them without circularity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from backend.gap_analyzer.enums import GapCommandStatus, GapJobKind, GapJobStatus


@dataclass(frozen=True)
class StoredGapSignalState:
    gap_question_id: UUID
    answer_confidence: float | None
    had_fallback: bool
    had_rejected: bool
    had_escalation: bool


@dataclass(frozen=True)
class ModeACorpusChunk:
    chunk_id: UUID
    document_id: UUID
    chunk_text: str
    vector: object
    filename: str | None
    source_url: str | None
    file_type: str
    section_title: str | None
    page_title: str | None


@dataclass(frozen=True)
class ModeADismissalRecord:
    topic_label: str
    topic_label_embedding: object


@dataclass(frozen=True)
class ModeBQuestionRecord:
    question_id: UUID
    question_text: str
    embedding: object
    gap_signal_weight: float
    language: str | None
    created_at: datetime


@dataclass(frozen=True)
class ModeBClusterRecord:
    cluster_id: UUID
    label: str | None
    centroid: object
    question_count: int
    aggregate_signal_weight: float
    coverage_score: float | None
    status: str
    last_question_at: datetime | None


@dataclass(frozen=True)
class TenantVectorMatch:
    score: float
    chunk_id: UUID


@dataclass(frozen=True)
class TenantBm25Match:
    hit: bool
    score: float
    match_kind: Literal["exact_title", "body", "none"]


@dataclass(frozen=True)
class GapJobEnqueueResult:
    status: GapCommandStatus
    enqueued: bool
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class GapJobRecord:
    job_id: UUID
    tenant_id: UUID
    job_kind: GapJobKind
    status: GapJobStatus
    trigger: str
    attempt_count: int
    max_attempts: int
