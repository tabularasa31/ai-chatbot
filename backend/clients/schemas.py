"""Pydantic schemas for client request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

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
    public_id: str
    has_openai_key: bool
    created_at: datetime
    updated_at: datetime


class ClientMeResponse(ClientResponse):
    """Extended client response for /clients/me with user context."""

    is_admin: bool
    is_verified: bool


class UpdateClientRequest(BaseModel):
    """Request body for updating a client."""

    name: str | None = None
    openai_api_key: str | None = None  # None = remove key


class ClientListResponse(BaseModel):
    """List of clients in API responses."""

    clients: list[ClientResponse]


class ValidateApiKeyResponse(BaseModel):
    """Response for API key validation (public endpoint)."""

    client_id: uuid.UUID
    name: str


class KycSecretGeneratedResponse(BaseModel):
    """One-time plaintext signing secret after generate or rotate."""

    secret_key: str
    message: str = "Store this securely. It will not be shown again."


class KycStatusResponse(BaseModel):
    """KYC / widget identity configuration status."""

    has_secret: bool
    identified_session_rate_7d: float
    last_identified_session: datetime | None = None
    masked_secret_hint: str | None = None


DisclosureLevelLiteral = Literal["detailed", "standard", "corporate"]
RedactionEntityLiteral = Literal["ID_DOC", "IP", "URL_TOKEN"]


class DisclosureConfigResponse(BaseModel):
    """Client-wide bot response detail level (all end-users)."""

    level: DisclosureLevelLiteral


class UpdateDisclosureConfigRequest(BaseModel):
    """PUT body for /clients/me/disclosure."""

    level: DisclosureLevelLiteral


class PrivacyConfigResponse(BaseModel):
    """Client-wide regex redaction settings."""

    optional_entity_types: list[RedactionEntityLiteral]


class UpdatePrivacyConfigRequest(BaseModel):
    """PUT body for /clients/me/privacy."""

    optional_entity_types: list[RedactionEntityLiteral]


class SupportSettingsResponse(BaseModel):
    """Client-wide support inbox settings."""

    l2_email: str | None = None
    escalation_language: str | None = None
    fallback_email: str | None = None


class UpdateSupportSettingsRequest(BaseModel):
    """PUT body for /clients/me/support-settings."""

    l2_email: str | None = None
    escalation_language: str | None = None

    @field_validator("l2_email")
    @classmethod
    def validate_l2_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip().lower()
        if not value:
            return None
        if value.count("@") != 1:
            raise ValueError("Enter a valid email address")
        local, domain = value.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("Enter a valid email address")
        return value
