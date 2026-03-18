"""Pydantic schemas for embedding API responses."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EmbeddingResponse(BaseModel):
    """Single embedding item in list response."""

    id: UUID
    document_id: UUID
    chunk_text: str = Field(..., description="First 100 chars of chunk")
    created_at: datetime


class EmbeddingListResponse(BaseModel):
    """List of embeddings for a document."""

    embeddings: list[EmbeddingResponse]
    total_chunks: int
