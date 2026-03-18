"""Pydantic schemas for client request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class CreateClientRequest(BaseModel):
    """Request body for creating a client."""

    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate name: min 3 chars, max 100."""
        if len(v) < 3:
            raise ValueError("Name must be at least 3 characters long")
        if len(v) > 100:
            raise ValueError("Name must be at most 100 characters")
        return v


class ClientResponse(BaseModel):
    """Client data in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    api_key: str
    has_openai_key: bool
    created_at: datetime
    updated_at: datetime


class ClientMeResponse(ClientResponse):
    """Extended client response for /clients/me with user context."""

    is_admin: bool
    is_verified: bool


class UpdateClientRequest(BaseModel):
    """Request body for updating a client."""

    name: Optional[str] = None
    openai_api_key: Optional[str] = None  # None = remove key


class ClientListResponse(BaseModel):
    """List of clients in API responses."""

    clients: list[ClientResponse]


class ValidateApiKeyResponse(BaseModel):
    """Response for API key validation (public endpoint)."""

    client_id: uuid.UUID
    name: str
