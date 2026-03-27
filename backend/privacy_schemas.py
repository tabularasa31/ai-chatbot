"""Shared schemas for privacy/original-content management."""

from __future__ import annotations

from pydantic import BaseModel


class DeletedCountResponse(BaseModel):
    deleted_count: int


OriginalContentDeleteResponse = DeletedCountResponse
