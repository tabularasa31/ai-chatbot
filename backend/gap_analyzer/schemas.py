"""DTOs for Gap Analyzer command and read surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from backend.gap_analyzer.enums import GapCommandStatus, GapDismissReason, GapRunMode, GapSource

GapItemStatus = Literal[
    "active",
    "closed",
    "dismissed",
    "inactive",
    "drafting",
    "in_review",
    "resolved",
]
GapClassification = Literal["uncovered", "partial", "covered", "unknown"]
ModeAStatusFilter = Literal["active", "dismissed", "archived", "all"]
ModeBStatusFilter = Literal[
    "active",
    "closed",
    "dismissed",
    "inactive",
    "drafting",
    "in_review",
    "resolved",
    "archived",
    "all",
]
ModeASort = Literal["coverage_asc", "newest"]
ModeBSort = Literal["signal_desc", "coverage_asc", "newest"]


class ModeAResult(BaseModel):
    tenant_id: UUID
    status: GapCommandStatus
    started_at: datetime | None = None


class ModeBResult(BaseModel):
    tenant_id: UUID
    status: GapCommandStatus
    started_at: datetime | None = None


class RecalculateCommandResult(BaseModel):
    tenant_id: UUID
    mode: GapRunMode
    status: GapCommandStatus
    command_kind: Literal["orchestration"] = "orchestration"
    http_status_code: Literal[202] = 202
    accepted_at: datetime | None = None
    retry_after_seconds: int | None = None


class GapItemResponse(BaseModel):
    id: UUID
    source: GapSource
    label: str
    coverage_score: float | None = None
    classification: GapClassification = "unknown"
    status: GapItemStatus
    is_new: bool = False
    question_count: int = 0
    aggregate_signal_weight: float | None = None
    example_questions: list[str] = []
    linked_source: GapSource | None = None
    linked_label: str | None = None
    linked_example_questions: list[str] = []
    also_missing_in_docs: bool = False
    last_updated: datetime | None = None
    has_draft: bool = False
    draft_updated_at: datetime | None = None
    published_faq_id: UUID | None = None


class GapSummaryResponse(BaseModel):
    total_active: int = 0
    uncovered_count: int = 0
    partial_count: int = 0
    impact_statement: str
    new_badge_count: int = 0
    last_updated: datetime | None = None


class GapAnalyzerResponse(BaseModel):
    summary: GapSummaryResponse
    mode_a_items: list[GapItemResponse]
    mode_b_items: list[GapItemResponse]


class GapSummaryOnlyResponse(BaseModel):
    summary: GapSummaryResponse


class GapDismissRequest(BaseModel):
    reason: GapDismissReason = GapDismissReason.other


class GapActionResponse(BaseModel):
    success: bool = True
    source: GapSource
    gap_id: UUID
    status: GapItemStatus


class GapDraftResponse(BaseModel):
    source: GapSource
    gap_id: UUID
    title: str
    markdown: str


class DraftPayload(BaseModel):
    """Mode B draft state stored on gap_clusters.draft_* columns."""

    gap_id: UUID
    title: str
    question: str
    markdown: str
    language: str
    draft_updated_at: datetime
    status: GapItemStatus


class RefineDraftRequest(BaseModel):
    guidance: str


class UpdateDraftRequest(BaseModel):
    title: str
    question: str
    markdown: str
    if_match: datetime


class PublishResult(BaseModel):
    gap_id: UUID
    faq_id: UUID
    status: GapItemStatus


class DiscardDraftResponse(BaseModel):
    gap_id: UUID
    status: GapItemStatus
