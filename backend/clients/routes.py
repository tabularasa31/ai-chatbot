"""FastAPI client management endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.clients.schemas import (
    ClientMeResponse,
    ClientResponse,
    CreateClientRequest,
    DisclosureConfigResponse,
    KycSecretGeneratedResponse,
    KycStatusResponse,
    PrivacyConfigResponse,
    SupportSettingsResponse,
    UpdateClientRequest,
    UpdateDisclosureConfigRequest,
    UpdatePrivacyConfigRequest,
    UpdateSupportSettingsRequest,
    ValidateApiKeyResponse,
)
from backend.clients.service import (
    create_client,
    delete_client,
    generate_kyc_secret_for_client,
    get_client_by_api_key,
    get_client_by_id,
    get_client_by_user,
    get_disclosure_config_for_user,
    get_kyc_status,
    get_redaction_config_for_user,
    get_support_settings_for_user,
    rotate_kyc_secret,
    update_client,
    update_disclosure_config_for_user,
    update_redaction_config_for_user,
    update_support_settings_for_user,
)
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.models import User

clients_router = APIRouter(tags=["clients"])


def _client_to_response(client) -> ClientResponse:
    return ClientResponse(
        id=client.id,
        name=client.name,
        api_key=client.api_key,
        public_id=client.public_id,
        has_openai_key=bool(client.openai_api_key),
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


@clients_router.post("", response_model=ClientResponse, status_code=201)
def create_client_route(
    body: CreateClientRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ClientResponse:
    """
    Create a client (protected JWT).

    Returns 201 Created. Error 409 if client already exists for this user.
    """
    client = create_client(current_user.id, body.name, db)
    return _client_to_response(client)


@clients_router.get("/me", response_model=ClientMeResponse)
def get_my_client(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ClientMeResponse:
    """
    Get current user's client (protected JWT).

    Returns 403 if email not verified, 404 if no client yet.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    base = _client_to_response(client)
    return ClientMeResponse(
        **base.model_dump(),
        is_admin=current_user.is_admin,
        is_verified=current_user.is_verified,
    )


@clients_router.post("/me/kyc/secret", response_model=KycSecretGeneratedResponse)
def create_kyc_secret_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KycSecretGeneratedResponse:
    """Generate and store KYC signing secret (returned once)."""
    _client, raw = generate_kyc_secret_for_client(current_user.id, db)
    return KycSecretGeneratedResponse(secret_key=raw)


@clients_router.post("/me/kyc/rotate", response_model=KycSecretGeneratedResponse)
def rotate_kyc_secret_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KycSecretGeneratedResponse:
    """Rotate signing secret; previous key remains valid for 1 hour."""
    _client, raw = rotate_kyc_secret(current_user.id, db)
    return KycSecretGeneratedResponse(secret_key=raw)


@clients_router.get("/me/kyc/status", response_model=KycStatusResponse)
def kyc_status_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KycStatusResponse:
    """Return KYC secret presence and identified-session metrics."""
    data = get_kyc_status(current_user.id, db)
    return KycStatusResponse(**data)


@clients_router.get("/me/disclosure", response_model=DisclosureConfigResponse)
def get_disclosure_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DisclosureConfigResponse:
    """Client-wide response detail level (same for all users and channels)."""
    data = get_disclosure_config_for_user(current_user.id, db)
    return DisclosureConfigResponse(**data)


@clients_router.put("/me/disclosure", response_model=DisclosureConfigResponse)
def put_disclosure_route(
    body: UpdateDisclosureConfigRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DisclosureConfigResponse:
    """Update client-wide disclosure level."""
    data = update_disclosure_config_for_user(current_user.id, body.level, db)
    return DisclosureConfigResponse(**data)


@clients_router.get("/me/privacy", response_model=PrivacyConfigResponse)
def get_privacy_route(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PrivacyConfigResponse:
    data = get_redaction_config_for_user(current_user.id, db)
    return PrivacyConfigResponse(**data)


@clients_router.put("/me/privacy", response_model=PrivacyConfigResponse)
def put_privacy_route(
    body: UpdatePrivacyConfigRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PrivacyConfigResponse:
    data = update_redaction_config_for_user(current_user.id, body.optional_entity_types, db)
    return PrivacyConfigResponse(**data)


@clients_router.get(
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


@clients_router.put(
    "/me/support-settings",
    response_model=SupportSettingsResponse,
    response_model_exclude_none=True,
)
def put_support_settings_route(
    body: UpdateSupportSettingsRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SupportSettingsResponse:
    # Pass only the fields the client explicitly included in the request body.
    # Absent fields are left unchanged so that older clients that do not know
    # about escalation_language cannot accidentally clear it.
    config: dict[str, str | None] = {k: getattr(body, k) for k in body.model_fields_set}
    data = update_support_settings_for_user(current_user.id, config, db)
    return SupportSettingsResponse(**data)


@clients_router.patch("/me", response_model=ClientResponse)
def update_my_client(
    body: UpdateClientRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ClientResponse:
    """
    Update current user's client (protected JWT).

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
        client = update_client(current_user.id, db, **update_kwargs)
    except RuntimeError as e:
        if "ENCRYPTION_KEY" in str(e):
            raise HTTPException(
                status_code=503,
                detail="Server misconfiguration: encryption is not configured. Contact support.",
            ) from e
        raise
    return _client_to_response(client)


@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
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
    client = get_client_by_api_key(api_key, db)
    if not client:
        raise HTTPException(status_code=404, detail="Invalid API key")
    return ValidateApiKeyResponse(client_id=client.id, name=client.name)


@clients_router.get("/{client_id}", response_model=ClientResponse)
def get_client_by_id_route(
    client_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ClientResponse:
    """
    Get client by UUID (protected JWT).

    Returns 404 if not found or not owner.
    """
    client = get_client_by_id(client_id, current_user.id, db)
    return _client_to_response(client)


@clients_router.delete("/{client_id}", status_code=204, response_model=None)
def delete_client_route(
    client_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """
    Delete client (protected JWT).

    Returns 204 No Content. Error 404 if not found or not owner.
    """
    delete_client(client_id, current_user.id, db)
