"""FastAPI tenant management endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.models import User
from backend.tenants.schemas import (
    CreateTenantRequest,
    DisclosureConfigResponse,
    KycSecretGeneratedResponse,
    KycStatusResponse,
    PrivacyConfigResponse,
    SupportSettingsResponse,
    TenantMeResponse,
    TenantResponse,
    UpdateDisclosureConfigRequest,
    UpdatePrivacyConfigRequest,
    UpdateSupportSettingsRequest,
    UpdateTenantRequest,
    ValidateApiKeyResponse,
)
from backend.tenants.service import (
    create_tenant,
    delete_tenant,
    generate_kyc_secret_for_tenant,
    get_disclosure_config_for_user,
    get_kyc_status,
    get_redaction_config_for_user,
    get_support_settings_for_user,
    get_tenant_by_api_key,
    get_tenant_by_id,
    get_tenant_by_user,
    rotate_kyc_secret,
    update_disclosure_config_for_user,
    update_redaction_config_for_user,
    update_support_settings_for_user,
    update_tenant,
)

tenants_router = APIRouter(tags=["tenants"])


def _tenant_to_response(tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        api_key=tenant.api_key,
        public_id=tenant.public_id,
        has_openai_key=bool(tenant.openai_api_key),
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@tenants_router.post("", response_model=TenantResponse, status_code=201)
def create_tenant_route(
    body: CreateTenantRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantResponse:
    """
    Create a tenant (protected JWT).

    Returns 201 Created. Error 409 if tenant already exists for this user.
    """
    tenant = create_tenant(current_user.id, body.name, db)
    return _tenant_to_response(tenant)


@tenants_router.get("/me", response_model=TenantMeResponse)
def get_my_client(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMeResponse:
    """
    Get current user's tenant (protected JWT).

    Returns 403 if email not verified, 404 if no tenant yet.
    """
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    base = _tenant_to_response(tenant)
    return TenantMeResponse(
        **base.model_dump(),
        is_admin=current_user.is_admin,
        is_verified=current_user.is_verified,
    )


@tenants_router.post("/me/kyc/secret", response_model=KycSecretGeneratedResponse)
def create_kyc_secret_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KycSecretGeneratedResponse:
    """Generate and store KYC signing secret (returned once)."""
    _client, raw = generate_kyc_secret_for_tenant(current_user.id, db)
    return KycSecretGeneratedResponse(secret_key=raw)


@tenants_router.post("/me/kyc/rotate", response_model=KycSecretGeneratedResponse)
def rotate_kyc_secret_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KycSecretGeneratedResponse:
    """Rotate signing secret; previous key remains valid for 1 hour."""
    _client, raw = rotate_kyc_secret(current_user.id, db)
    return KycSecretGeneratedResponse(secret_key=raw)


@tenants_router.get("/me/kyc/status", response_model=KycStatusResponse)
def kyc_status_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KycStatusResponse:
    """Return KYC secret presence and identified-session metrics."""
    data = get_kyc_status(current_user.id, db)
    return KycStatusResponse(**data)


@tenants_router.get("/me/disclosure", response_model=DisclosureConfigResponse)
def get_disclosure_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DisclosureConfigResponse:
    """Tenant-wide response detail level (same for all users and channels)."""
    data = get_disclosure_config_for_user(current_user.id, db)
    return DisclosureConfigResponse(**data)


@tenants_router.put("/me/disclosure", response_model=DisclosureConfigResponse)
def put_disclosure_route(
    body: UpdateDisclosureConfigRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DisclosureConfigResponse:
    """Update tenant-wide disclosure level."""
    data = update_disclosure_config_for_user(current_user.id, body.level, db)
    return DisclosureConfigResponse(**data)


@tenants_router.get("/me/privacy", response_model=PrivacyConfigResponse)
def get_privacy_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PrivacyConfigResponse:
    data = get_redaction_config_for_user(current_user.id, db)
    return PrivacyConfigResponse(**data)


@tenants_router.put("/me/privacy", response_model=PrivacyConfigResponse)
def put_privacy_route(
    body: UpdatePrivacyConfigRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PrivacyConfigResponse:
    data = update_redaction_config_for_user(current_user.id, body.optional_entity_types, db)
    return PrivacyConfigResponse(**data)


@tenants_router.get(
    "/me/support-settings",
    response_model=SupportSettingsResponse,
    response_model_exclude_none=True,
)
def get_support_settings_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SupportSettingsResponse:
    data = get_support_settings_for_user(current_user.id, db)
    return SupportSettingsResponse(**data)


@tenants_router.put(
    "/me/support-settings",
    response_model=SupportSettingsResponse,
    response_model_exclude_none=True,
)
def put_support_settings_route(
    body: UpdateSupportSettingsRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SupportSettingsResponse:
    # Pass only the fields the tenant explicitly included in the request body.
    # Absent fields are left unchanged so that older tenants that do not know
    # about escalation_language cannot accidentally clear it.
    config: dict[str, str | None] = {k: getattr(body, k) for k in body.model_fields_set}
    data = update_support_settings_for_user(current_user.id, config, db)
    return SupportSettingsResponse(**data)


@tenants_router.patch("/me", response_model=TenantResponse)
def update_my_client(
    body: UpdateTenantRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantResponse:
    """
    Update current user's tenant (protected JWT).

    openai_api_key: set to update, null/empty to remove. Omit to leave unchanged.
    Validates key starts with "sk-" if provided.
    """
    update_kwargs: dict = {}
    if "name" in body.model_fields_set:
        update_kwargs["name"] = body.name
    if "openai_api_key" in body.model_fields_set:
        raw = body.openai_api_key
        key_val = raw.strip() if raw else None
        if key_val and not key_val.startswith("sk-"):
            raise HTTPException(
                status_code=400,
                detail="OpenAI API key must start with 'sk-'",
            )
        update_kwargs["openai_api_key"] = key_val
    try:
        tenant = update_tenant(current_user.id, db, **update_kwargs)
    except RuntimeError as e:
        if "ENCRYPTION_KEY" in str(e):
            raise HTTPException(
                status_code=503,
                detail="Server misconfiguration: encryption is not configured. Contact support.",
            ) from e
        raise
    return _tenant_to_response(tenant)


@tenants_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
@limiter.limit("20/minute")
def validate_api_key(
    request: Request,
    api_key: str,
    db: Annotated[Session, Depends(get_db)],
) -> ValidateApiKeyResponse:
    """
    Validate API key (PUBLIC — no JWT needed).

    Used by chat/widget to validate API key.
    Returns 404 if invalid API key.
    """
    tenant = get_tenant_by_api_key(api_key, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Invalid API key")
    return ValidateApiKeyResponse(tenant_id=tenant.id, name=tenant.name)


@tenants_router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant_by_id_route(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantResponse:
    """
    Get tenant by UUID (protected JWT).

    Returns 404 if not found or not owner.
    """
    tenant = get_tenant_by_id(tenant_id, current_user.id, db)
    return _tenant_to_response(tenant)


@tenants_router.delete("/{tenant_id}", status_code=204, response_model=None)
def delete_tenant_route(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """
    Delete tenant (protected JWT).

    Returns 204 No Content. Error 404 if not found or not owner.
    """
    delete_tenant(tenant_id, current_user.id, db)
