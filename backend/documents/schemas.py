"""Pydantic schemas for document request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

SOURCE_TYPE_URL = "url"
UrlSourceSchedule = Literal["daily", "weekly", "manual"]
UrlSourceType = Literal["url"]


class DocumentResponse(BaseModel):
    """Document data in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    health_status: Optional[dict[str, Any]] = None


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
    parsed_text: Optional[str]
    health_status: Optional[dict[str, Any]] = None


class DocumentHealthStatusResponse(BaseModel):
    """Stored document health check result (from DB)."""

    score: Optional[int] = None
    checked_at: str
    warnings: list[dict[str, Any]]
    error: Optional[str] = None


class UrlSourceCreateRequest(BaseModel):
    url: AnyHttpUrl
    name: Optional[str] = None
    schedule: UrlSourceSchedule = "weekly"
    exclusions: list[str] = Field(default_factory=list)


class UrlSourceUpdateRequest(BaseModel):
    name: Optional[str] = None
    schedule: Optional[UrlSourceSchedule] = None
    exclusions: Optional[list[str]] = None


class UrlSourceRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    pages_found: Optional[int] = None
    pages_indexed: int
    failed_urls: list[dict[str, Any]]
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


class SourcePageResponse(BaseModel):
    id: uuid.UUID
    title: str
    url: str
    chunk_count: int
    updated_at: datetime


class UrlSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    source_type: UrlSourceType = SOURCE_TYPE_URL
    status: str
    schedule: UrlSourceSchedule
    pages_found: Optional[int] = None
    pages_indexed: int
    chunks_created: int
    last_crawled_at: Optional[datetime] = None
    next_crawl_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    warning_message: Optional[str] = None
    error_message: Optional[str] = None
    exclusion_patterns: list[str] = Field(default_factory=list)


class UrlSourceDetailResponse(UrlSourceResponse):
    recent_runs: list[UrlSourceRunResponse]
    pages: list[SourcePageResponse]


class KnowledgeSourcesResponse(BaseModel):
    documents: list[DocumentResponse]
    url_sources: list[UrlSourceResponse]
