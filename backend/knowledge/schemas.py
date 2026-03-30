from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeProfileResponse(BaseModel):
    product_name: Optional[str]
    modules: list[str] = Field(default_factory=list)
    glossary: list[dict] = Field(default_factory=list)
    support_email: Optional[str]
    support_urls: list[str] = Field(default_factory=list)
    aliases: list[dict] = Field(default_factory=list)
    updated_at: datetime
    extraction_status: Literal["pending", "done", "failed"]


class KnowledgeProfilePatchRequest(BaseModel):
    product_name: Optional[str] = None
    modules: Optional[list[str]] = None
    glossary: Optional[list[dict]] = None
    support_email: Optional[str] = None
    support_urls: Optional[list[str]] = None


class KnowledgeFaqItemResponse(BaseModel):
    id: UUID
    question: str
    answer: str
    confidence: Optional[float]
    source: Optional[str]
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

