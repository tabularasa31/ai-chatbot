"""Pydantic schemas for client request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class CreateTenantRequest(BaseModel):
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


class TenantResponse(BaseModel):
    """Tenant data in API responses.

    The widget API key is not returned here — it is only ever surfaced
    once via /api-keys/rotate. Use ``api_key_hint`` (last 4 chars of the
    primary active key) to identify the active key in the UI.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    api_key_hint: str | None = None
    public_id: str
    has_openai_key: bool
    created_at: datetime
    updated_at: datetime


class TenantApiKeyResponse(BaseModel):
    """A single tenant API key as exposed in the dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key_hint: str
    status: Literal["active", "revoking", "revoked"]
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    last_used_at: datetime | None = None


class TenantApiKeyListResponse(BaseModel):
    items: list[TenantApiKeyResponse]


class RotateTenantApiKeyRequest(BaseModel):
    reason: Literal["leaked", "scheduled", "compromise", "other"] = "scheduled"
    revoke_old_immediately: bool = False


class RotateTenantApiKeyResponse(BaseModel):
    """One-time plaintext key surfaced after a successful rotation."""

    api_key: str
    key: TenantApiKeyResponse
    message: str = "Store this key securely. It will not be shown again."


class TenantMeResponse(TenantResponse):
    """Extended client response for /clients/me with user context."""

    is_admin: bool
    is_verified: bool


class CreateTenantResponse(TenantResponse):
    """Returned once when a tenant is created. Includes plaintext widget
    key — this is the only point in the API where it is exposed."""

    api_key: str


class UpdateTenantRequest(BaseModel):
    """Request body for updating a client."""

    name: str | None = None
    openai_api_key: str | None = None  # None = remove key


class TenantListResponse(BaseModel):
    """List of clients in API responses."""

    clients: list[TenantResponse]


RedactionEntityLiteral = Literal["ID_DOC", "IP", "URL_TOKEN"]


class PrivacyConfigResponse(BaseModel):
    """Tenant-wide regex redaction settings."""

    optional_entity_types: list[RedactionEntityLiteral]


class UpdatePrivacyConfigRequest(BaseModel):
    """PUT body for /clients/me/privacy."""

    optional_entity_types: list[RedactionEntityLiteral]


class SupportSettingsResponse(BaseModel):
    """Tenant-wide support inbox settings."""

    l2_email: str | None = None
    escalation_language: str | None = None
    fallback_email: str | None = None


class TenantLlmAlertResponse(BaseModel):
    """Active LLM-failure alert for the tenant dashboard banner.

    `type` is `null` when no alert is active. Possible non-null values match
    `backend.chat.llm_unavailable.LlmFailureType` (currently
    `quota_exhausted` or `invalid_api_key` — only actionable failures
    raise an alert).
    """

    type: str | None = None
    since: datetime | None = None


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
