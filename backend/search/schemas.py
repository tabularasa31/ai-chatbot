"""Pydantic schemas for search API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Request body for vector search."""

    query: str = Field(..., min_length=1, description="Search query text")
    top_k: int = Field(default=3, ge=1, le=100, description="Number of top results to return")


class SearchResultItem(BaseModel):
    """Single search result item."""

    document_id: UUID
    chunk_text: str
    similarity: float
    chunk_index: int


class SearchResponse(BaseModel):
    """Response containing search results."""

    results: list[SearchResultItem]
