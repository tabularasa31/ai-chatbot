from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeProfileResponse(BaseModel):
    product_name: str | None
    topics: list[str] = Field(default_factory=list)
    glossary: list[dict] = Field(default_factory=list)
    support_email: str | None
    support_urls: list[str] = Field(default_factory=list)
    aliases: list[dict] = Field(default_factory=list)
    updated_at: datetime
    extraction_status: Literal["pending", "done", "failed"]


class KnowledgeProfilePatchRequest(BaseModel):
    product_name: str | None = None
    topics: list[str] | None = None
    glossary: list[dict] | None = None
    support_email: str | None = None
    support_urls: list[str] | None = None


class KnowledgeFaqItemResponse(BaseModel):
    id: UUID
    question: str
    answer: str
    confidence: float | None
    source: str | None
    approved: bool
    created_at: datetime


class KnowledgeFaqListResponse(BaseModel):
    items: list[KnowledgeFaqItemResponse]
    total: int
    pending_count: int


class KnowledgeFaqApproveResponse(BaseModel):
    id: UUID
    approved: bool


class KnowledgeFaqRejectResponse(BaseModel):
    id: UUID
    deleted: bool


class KnowledgeFaqApproveAllResponse(BaseModel):
    approved_count: int


class KnowledgeFaqUpdateRequest(BaseModel):
    question: str
    answer: str
