from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class KnowledgeProfileResponse(BaseModel):
    product_name: Optional[str]
    topics: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    glossary: list[dict] = Field(default_factory=list)
    support_email: Optional[str]
    support_urls: list[str] = Field(default_factory=list)
    aliases: list[dict] = Field(default_factory=list)
    updated_at: datetime
    extraction_status: Literal["pending", "done", "failed"]


class KnowledgeProfilePatchRequest(BaseModel):
    product_name: Optional[str] = None
    topics: Optional[list[str]] = None
    modules: Optional[list[str]] = None
    glossary: Optional[list[dict]] = None
    support_email: Optional[str] = None
    support_urls: Optional[list[str]] = None

    @model_validator(mode="after")
    def _sync_topics_and_modules(self) -> "KnowledgeProfilePatchRequest":
        if self.topics is None and self.modules is not None:
            self.topics = list(self.modules)
        elif self.modules is None and self.topics is not None:
            self.modules = list(self.topics)
        return self


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
