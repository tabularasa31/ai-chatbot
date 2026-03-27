"""Shared schemas for privacy/original-content management."""

from __future__ import annotations

from pydantic import BaseModel


class OriginalContentDeleteResponse(BaseModel):
    deleted_count: int
