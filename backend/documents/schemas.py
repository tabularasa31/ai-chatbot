"""Pydantic schemas for document request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

SOURCE_TYPE_URL = "url"
UrlSourceSchedule = Literal["daily", "weekly", "manual"]
UrlSourceType = Literal["url"]
ExclusionPattern = Annotated[str, Field(max_length=255)]


class DocumentResponse(BaseModel):
    """Document data in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    health_status: dict[str, Any] | None = None


class DocumentListResponse(BaseModel):
    """List of documents in API responses."""

    documents: list[DocumentResponse]


class DocumentDetailResponse(BaseModel):
    """Document detail with parsed_text preview (first 500 chars)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    parsed_text: str | None
    health_status: dict[str, Any] | None = None


class DocumentHealthStatusResponse(BaseModel):
    """Stored document health check result (from DB)."""

    score: int | None = None
    checked_at: str
    warnings: list[dict[str, Any]]
    error: str | None = None


class UrlSourceCreateRequest(BaseModel):
    url: AnyHttpUrl
    name: str | None = None
    schedule: UrlSourceSchedule = "weekly"
    exclusions: list[ExclusionPattern] = Field(default_factory=list, max_length=50)


class UrlSourceUpdateRequest(BaseModel):
    name: str | None = None
    schedule: UrlSourceSchedule | None = None
    exclusions: list[ExclusionPattern] | None = Field(default=None, max_length=50)


class UrlSourceFailureResponse(BaseModel):
    url: str
    reason: str


class UrlSourceRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    pages_found: int | None = None
    pages_indexed: int
    failed_urls: list[UrlSourceFailureResponse]
    duration_seconds: int | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class SourcePageResponse(BaseModel):
    id: uuid.UUID
    title: str
    url: str
    chunk_count: int
    updated_at: datetime


class QuickAnswerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    source_url: str
    detected_at: datetime


class UrlSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    source_type: UrlSourceType = SOURCE_TYPE_URL
    status: str
    schedule: UrlSourceSchedule
    pages_found: int | None = None
    pages_indexed: int
    chunks_created: int
    last_crawled_at: datetime | None = None
    next_crawl_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    warning_message: str | None = None
    error_message: str | None = None
    exclusion_patterns: list[str] = Field(default_factory=list)


class UrlSourceDetailResponse(UrlSourceResponse):
    recent_runs: list[UrlSourceRunResponse]
    pages: list[SourcePageResponse]
    quick_answers: list[QuickAnswerResponse] = Field(default_factory=list)


class KnowledgeSourcesResponse(BaseModel):
    documents: list[DocumentResponse]
    url_sources: list[UrlSourceResponse]
