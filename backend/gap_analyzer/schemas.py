"""Phase 1 DTOs for Gap Analyzer command surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel
from backend.gap_analyzer.enums import GapCommandStatus, GapDismissReason, GapRunMode


DismissReason = GapDismissReason


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
