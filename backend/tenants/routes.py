"""FastAPI tenant management endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.middleware import (
    get_current_tenant,
    require_member,
    require_owner,
    require_verified_user,
)
from backend.core.db import get_db
from backend.core.limiter import limiter, owner_jwt_rate_limit_key
from backend.models import RerankerStrategy, Tenant, User
from backend.seats.service import holds_seat
from backend.tenants.schemas import (
    CreateTenantRequest,
    CreateTenantResponse,
    SupportSettingsResponse,
    TenantLlmAlertResponse,
    TenantMeResponse,
    TenantResponse,
    UpdateSupportSettingsRequest,
    UpdateTenantRequest,
)
from backend.tenants.service import (
    create_tenant,
    delete_tenant,
    get_support_settings_for_user,
    update_support_settings_for_user,
    update_tenant,
)

tenants_router = APIRouter(tags=["tenants"])


def _tenant_to_response(tenant, db: Session | None = None) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        public_id=tenant.public_id,
        has_openai_key=bool(tenant.openai_api_key),
        reranker_strategy=_reranker_strategy_name(tenant.reranker_strategy),
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


def _reranker_strategy_name(value) -> str:
    return value.value if isinstance(value, RerankerStrategy) else (value or "heuristic")


@tenants_router.post("", response_model=CreateTenantResponse, status_code=201, include_in_schema=False)
def create_tenant_route(
    body: CreateTenantRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CreateTenantResponse:
    """
    Create a tenant (protected JWT).

    Error 409 if tenant already exists for this user.
    """
    tenant = create_tenant(current_user.id, body.name, db)
    return CreateTenantResponse(**_tenant_to_response(tenant, db).model_dump())


@tenants_router.get("/me", response_model=TenantMeResponse)
def get_my_client(
    current_user: Annotated[User, Depends(require_verified_user)],
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMeResponse:
    """
    Get current user's tenant (protected JWT).

    Returns 403 if email not verified, 404 if no tenant yet.
    """
    base = _tenant_to_response(tenant, db)
    return TenantMeResponse(
        **base.model_dump(),
        is_admin=current_user.is_admin,
        is_verified=current_user.is_verified,
        role=current_user.role,
        has_seat=holds_seat(current_user),
    )


@tenants_router.get("/me/llm-alert", response_model=TenantLlmAlertResponse)
def get_llm_alert_route(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> TenantLlmAlertResponse:
    """Active LLM-failure alert for the dashboard banner.

    Returns ``{type: null}`` when nothing is wrong. Cleared automatically
    on the next successful chat turn (no manual dismiss endpoint — the
    banner reflects live state, not a sticky notification).
    """
    return TenantLlmAlertResponse(
        type=tenant.llm_alert_type,
        since=tenant.llm_alert_first_at,
    )


@tenants_router.get(
    "/me/support-settings",
    response_model=SupportSettingsResponse,
    response_model_exclude_none=True,
)
def get_support_settings_route(
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[Session, Depends(get_db)],
) -> SupportSettingsResponse:
    """Readable by any member, writable only by an owner.

    These are the support contacts the bot hands out to visitors, so the
    operator working the inbox is exactly who gets asked about them.
    """
    data = get_support_settings_for_user(current_user.id, db)
    return SupportSettingsResponse(**data)


@tenants_router.put(
    "/me/support-settings",
    response_model=SupportSettingsResponse,
    response_model_exclude_none=True,
)
def put_support_settings_route(
    body: UpdateSupportSettingsRequest,
    current_user: Annotated[User, Depends(require_owner)],
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
    current_user: Annotated[User, Depends(require_owner)],
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
    if "reranker_strategy" in body.model_fields_set and body.reranker_strategy is not None:
        update_kwargs["reranker_strategy"] = RerankerStrategy(body.reranker_strategy)
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


@tenants_router.delete(
    "/{tenant_id}",
    status_code=204,
    response_model=None,
    summary="Delete your workspace",
)
# The most destructive route in the file, and now a documented one. The
# neighbouring key routes are limited at 10-20/hour; this needs far less, since
# an owner has exactly one workspace and succeeding once leaves them with no
# credentials to try again.
@limiter.limit("5/hour", key_func=owner_jwt_rate_limit_key)
def delete_tenant_route(
    request: Request,
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Permanently delete your workspace. Owner only, and irreversible.

    A workspace has one owner, fixed at creation and impossible to hand over,
    so this is the only exit — which is why it is documented rather than
    hidden. Hiding it made the exit harder to *find* without making it any
    harder to perform.

    Deletes everything we hold for the workspace: conversations, escalation
    tickets, documents and their embeddings, API keys, and every account
    belonging to it — the owner's included, so the caller's own credentials
    stop working the moment this returns. Data in the systems we send to
    (Langfuse traces, Brevo contacts) is erased by a background job scheduled
    before the rows are removed. Copies already taken out of those systems by
    somebody — an export, a screenshot — are beyond our reach.

    There is no grace period and no recovery: no undo, no support-side restore.
    The widget stops answering on the tenant's site immediately.

    The path id is the confirmation: it must be the caller's own workspace.
    The dashboard additionally makes the owner type the workspace name.

    Returns 204 No Content. 404 if the workspace is not the caller's, 403 if
    the caller is not its owner, 503 if the external cleanup could not be
    scheduled — in which case nothing was deleted and the call can be retried.
    """
    delete_tenant(tenant_id, current_user.id, db)
