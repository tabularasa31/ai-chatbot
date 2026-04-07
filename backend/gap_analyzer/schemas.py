"""Phase 1 DTOs for Gap Analyzer command surfaces."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class GapRunMode(str, Enum):
    mode_a = "mode_a"
    mode_b = "mode_b"
    both = "both"


class GapCommandStatus(str, Enum):
    accepted = "accepted"
    in_progress = "in_progress"
    rate_limited = "rate_limited"


class DismissReason(str, Enum):
    feature_request = "feature_request"
    not_relevant = "not_relevant"
    already_covered = "already_covered"
    other = "other"


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
