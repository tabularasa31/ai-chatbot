"""DTOs for Gap Analyzer command and read surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel
from backend.gap_analyzer.enums import GapCommandStatus, GapDismissReason, GapRunMode, GapSource


DismissReason = GapDismissReason

GapItemStatus = Literal["active", "closed", "dismissed", "inactive"]
GapClassification = Literal["uncovered", "partial", "covered", "unknown"]
ModeAStatusFilter = Literal["active", "dismissed", "archived", "all"]
ModeBStatusFilter = Literal["active", "closed", "dismissed", "inactive", "archived", "all"]
ModeASort = Literal["coverage_asc", "newest"]
ModeBSort = Literal["signal_desc", "coverage_asc", "newest"]


class ModeAResult(BaseModel):
    tenant_id: UUID
    status: GapCommandStatus
    started_at: Optional[datetime] = None


class ModeBResult(BaseModel):
    tenant_id: UUID
    status: GapCommandStatus
    started_at: Optional[datetime] = None


class RecalculateCommandResult(BaseModel):
    tenant_id: UUID
    mode: GapRunMode
    status: GapCommandStatus
    command_kind: Literal["orchestration"] = "orchestration"
    http_status_code: Literal[202] = 202
    accepted_at: Optional[datetime] = None
    retry_after_seconds: Optional[int] = None


class GapItemResponse(BaseModel):
    id: UUID
    source: GapSource
    label: str
    coverage_score: Optional[float] = None
    classification: GapClassification = "unknown"
    status: GapItemStatus
    is_new: bool = False
    question_count: int = 0
    aggregate_signal_weight: Optional[float] = None
    example_questions: list[str] = []
    linked_source: Optional[GapSource] = None
    linked_label: Optional[str] = None
    linked_example_questions: list[str] = []
    also_missing_in_docs: bool = False
    last_updated: Optional[datetime] = None


class GapSummaryResponse(BaseModel):
    total_active: int = 0
    uncovered_count: int = 0
    partial_count: int = 0
    impact_statement: str
    new_badge_count: int = 0
    last_updated: Optional[datetime] = None


class GapAnalyzerResponse(BaseModel):
    summary: GapSummaryResponse
    mode_a_items: list[GapItemResponse]
    mode_b_items: list[GapItemResponse]


class GapSummaryOnlyResponse(BaseModel):
    summary: GapSummaryResponse


class GapDismissRequest(BaseModel):
    reason: DismissReason = DismissReason.other


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
