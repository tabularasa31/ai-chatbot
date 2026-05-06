"""FastAPI tenant management endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.core.db import get_db
from backend.core.limiter import limiter, owner_jwt_rate_limit_key
from backend.models import User
from backend.observability.metrics import capture_event, group_identify
from backend.tenants.api_keys_service import (
    assert_owner,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
)
from backend.tenants.schemas import (
    CreateTenantRequest,
    CreateTenantResponse,
    PrivacyConfigResponse,
    RotateTenantApiKeyRequest,
    RotateTenantApiKeyResponse,
    SupportSettingsResponse,
    TenantApiKeyListResponse,
    TenantApiKeyResponse,
    TenantMeResponse,
    TenantResponse,
    UpdatePrivacyConfigRequest,
    UpdateSupportSettingsRequest,
    UpdateTenantRequest,
)
from backend.tenants.service import (
    create_tenant,
    delete_tenant,
    get_primary_api_key_hint,
    get_redaction_config_for_user,
    get_support_settings_for_user,
    get_tenant_by_id,
    get_tenant_by_user,
    update_redaction_config_for_user,
    update_support_settings_for_user,
    update_tenant,
)

tenants_router = APIRouter(tags=["tenants"])


def _tenant_to_response(tenant, db: Session | None = None) -> TenantResponse:
    hint = get_primary_api_key_hint(tenant.id, db) if db is not None else None
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        api_key_hint=hint,
        public_id=tenant.public_id,
        has_openai_key=bool(tenant.openai_api_key),
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@tenants_router.post("", response_model=CreateTenantResponse, status_code=201, include_in_schema=False)
def create_tenant_route(
    body: CreateTenantRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CreateTenantResponse:
    """
    Create a tenant (protected JWT).

    Returns 201 Created with the plaintext widget API key — the only
    time it is shown. Error 409 if tenant already exists for this user.
    """
    tenant, plaintext = create_tenant(current_user.id, body.name, db)
    try:
        tenant_id = str(tenant.public_id)
        group_identify("tenant", tenant_id, {"name": body.name})
        capture_event(
            "tenant.created",
            distinct_id=tenant_id,
            tenant_id=tenant_id,
            groups={"tenant": tenant_id},
        )
    except Exception:
        pass
    base = _tenant_to_response(tenant, db)
    return CreateTenantResponse(**base.model_dump(), api_key=plaintext)


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
    base = _tenant_to_response(tenant, db)
    return TenantMeResponse(
        **base.model_dump(),
        is_admin=current_user.is_admin,
        is_verified=current_user.is_verified,
    )


@tenants_router.get(
    "/me/api-keys",
    response_model=TenantApiKeyListResponse,
)
def list_api_keys_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantApiKeyListResponse:
    """List widget API keys for the current tenant (no plaintext)."""
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    rows = list_api_keys(tenant.id, db)
    return TenantApiKeyListResponse(
        items=[TenantApiKeyResponse.model_validate(r) for r in rows]
    )


@tenants_router.post(
    "/me/api-keys/rotate",
    response_model=RotateTenantApiKeyResponse,
    status_code=201,
)
@limiter.limit("10/hour", key_func=owner_jwt_rate_limit_key)
def rotate_api_key_route(
    request: Request,
    body: RotateTenantApiKeyRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RotateTenantApiKeyResponse:
    """Issue a new widget API key. Existing active key enters a 24h
    grace window unless ``revoke_old_immediately`` is set."""
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    assert_owner(current_user, tenant.id)
    new_row, plaintext = rotate_api_key(
        tenant.id,
        db,
        reason=body.reason,
        revoke_old_immediately=body.revoke_old_immediately,
        actor_user_id=current_user.id,
    )
    try:
        capture_event(
            "tenant.api_key.rotated",
            distinct_id=str(tenant.public_id),
            tenant_id=str(tenant.public_id),
            properties={
                "reason": body.reason,
                "revoke_old_immediately": body.revoke_old_immediately,
            },
        )
    except Exception:
        pass
    return RotateTenantApiKeyResponse(
        api_key=plaintext,
        key=TenantApiKeyResponse.model_validate(new_row),
    )


@tenants_router.delete(
    "/me/api-keys/{key_id}",
    response_model=TenantApiKeyResponse,
)
@limiter.limit("20/hour", key_func=owner_jwt_rate_limit_key)
def revoke_api_key_route(
    request: Request,
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantApiKeyResponse:
    """Immediately revoke a single key (no grace). Refuses if it would
    leave the tenant with no usable key."""
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    assert_owner(current_user, tenant.id)
    row = revoke_api_key(tenant.id, key_id, db, reason="manual")
    try:
        capture_event(
            "tenant.api_key.revoked",
            distinct_id=str(tenant.public_id),
            tenant_id=str(tenant.public_id),
            properties={"key_id": str(key_id)},
        )
    except Exception:
        pass
    return TenantApiKeyResponse.model_validate(row)


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
    return _tenant_to_response(tenant, db)


@tenants_router.get("/{tenant_id}", response_model=TenantResponse, include_in_schema=False)
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
    return _tenant_to_response(tenant, db)


@tenants_router.delete("/{tenant_id}", status_code=204, response_model=None, include_in_schema=False)
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
