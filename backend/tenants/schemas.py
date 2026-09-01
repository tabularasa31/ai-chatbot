"""Pydantic schemas for client request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

#: What the API REPORTS. Deliberately open where the request type is closed.
#: ``users.role`` is a plain ``String(32)`` precisely so a third role needs no
#: data migration — but a closed response type turns the first row holding one
#: into a 500 on ``GET /tenants/me``, which the dashboard shell and the
#: sidebar's role check both call on mount, so the whole app breaks for that
#: user instead of degrading. It is reachable without any bug: deploy a build
#: that adds ``admin``, let it write one row, roll back. Reporting the value
#: truthfully is both more honest and safer — every consumer tests for
#: ``owner`` explicitly and treats anything else as less privileged, so an
#: unrecognised role loses access rather than gaining it.
TenantRole = str

#: Derived, not stored: a member who has not yet set a password from their
#: invite link is ``pending``. See ``members_service`` for why that needs no
#: column of its own.
TenantMemberStatus = Literal["active", "pending"]


class TenantMemberResponse(BaseModel):
    """One row of the members screen."""

    id: uuid.UUID
    email: str
    role: TenantRole
    status: TenantMemberStatus
    created_at: datetime
    #: When this person's operator seat was granted; ``null`` for no seat.
    #: Invited members are seated by the invite itself, so in practice only a
    #: workspace's founding owner is ever ``null`` here.
    seat_granted_at: datetime | None = None


class TenantMemberListResponse(BaseModel):
    """All members of the current workspace, and what they are priced at."""

    items: list[TenantMemberResponse]
    #: How many of them hold a seat. Sent rather than left to the client to
    #: count, so the figure the seats screen prices is the server's.
    seats: int = 0


class InviteMemberRequest(BaseModel):
    """Request body for inviting someone into the workspace.

    No role: every invitee is an operator. The workspace's one owner is the
    person who created it, and a role on this body would be a way to mint a
    second.
    """

    email: EmailStr


class InviteMemberResponse(BaseModel):
    """Result of an invite. A set-password link is always sent — the account
    created for the invitee has no usable password until they follow it."""

    member: TenantMemberResponse


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
    role: TenantRole


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
