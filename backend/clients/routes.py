"""FastAPI client management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_user
from backend.clients.schemas import (
    ClientMeResponse,
    ClientResponse,
    CreateClientRequest,
    UpdateClientRequest,
    ValidateApiKeyResponse,
)
from backend.clients.service import (
    create_client,
    delete_client,
    get_client_by_api_key,
    get_client_by_id,
    get_client_by_user,
    update_client,
)
from backend.core.db import get_db
from backend.models import User

clients_router = APIRouter(tags=["clients"])


def _client_to_response(client) -> ClientResponse:
    return ClientResponse(
        id=client.id,
        name=client.name,
        api_key=client.api_key,
        has_openai_key=bool(client.openai_api_key),
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


@clients_router.post("", response_model=ClientResponse, status_code=201)
def create_client_route(
    body: CreateClientRequest,
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ClientMeResponse:
    """
    Get current user's client (protected JWT).

    Returns 404 if no client yet.
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


@clients_router.patch("/me", response_model=ClientResponse)
def update_my_client(
    body: UpdateClientRequest,
    current_user: Annotated[User, Depends(get_current_user)],
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
    client = update_client(current_user.id, db, **update_kwargs)
    return _client_to_response(client)


@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
def validate_api_key(
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
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """
    Delete client (protected JWT).

    Returns 204 No Content. Error 404 if not found or not owner.
    """
    delete_client(client_id, current_user.id, db)
