"""Pydantic schemas for document request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    """Document data in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    status: str
    created_at: datetime
    updated_at: datetime


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
