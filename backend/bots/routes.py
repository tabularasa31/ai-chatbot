from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.bots import service as bots_service
from backend.bots.schemas import (
    BotCreate,
    BotList,
    BotResponse,
    BotUpdate,
    DisclosureConfigResponse,
    DisclosureConfigUpdate,
)
from backend.core.crypto import decrypt_value
from backend.core.db import get_db
from backend.models import Tenant, User

bots_router = APIRouter(prefix="/bots", tags=["bots"])


def _current_user(
    current_user: Annotated[User, Depends(require_verified_user)],
) -> User:
    if not current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return current_user


def _tenant_id(
    current_user: Annotated[User, Depends(_current_user)],
) -> uuid.UUID:
    return current_user.tenant_id  # type: ignore[return-value]


@bots_router.get("", response_model=BotList)
def list_bots(
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> BotList:
    bots = bots_service.get_bots_for_tenant(tenant_id, db)
    return BotList(items=bots)


@bots_router.post("", response_model=BotResponse, status_code=201)
def create_bot(
    body: BotCreate,
    current_user: Annotated[User, Depends(_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BotResponse:
    tenant_id: uuid.UUID = current_user.tenant_id  # type: ignore[assignment]
    api_key: str | None = None
    if body.website_url:
        tenant = db.get(Tenant, tenant_id)
        if tenant and tenant.openai_api_key:
            try:
                api_key = decrypt_value(tenant.openai_api_key)
            except Exception:
                api_key = None
    return bots_service.create_bot(
        tenant_id,
        body.name,
        db,
        agent_instructions=body.agent_instructions,
        website_url=body.website_url,
        api_key=api_key,
    )


@bots_router.get("/{bot_id}", response_model=BotResponse)
def get_bot(
    bot_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> BotResponse:
    return bots_service.get_bot_by_id(bot_id, tenant_id, db)


@bots_router.patch("/{bot_id}", response_model=BotResponse)
def update_bot(
    bot_id: uuid.UUID,
    body: BotUpdate,
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> BotResponse:
    return bots_service.update_bot(
        bot_id,
        tenant_id,
        db,
        name=body.name,
        is_active=body.is_active,
        agent_instructions=body.agent_instructions,
    )


@bots_router.delete("/{bot_id}", status_code=204, response_model=None)
def delete_bot(
    bot_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    bots_service.delete_bot(bot_id, tenant_id, db)


@bots_router.get("/{bot_id}/disclosure", response_model=DisclosureConfigResponse)
def get_bot_disclosure(
    bot_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> DisclosureConfigResponse:
    data = bots_service.get_bot_disclosure_config(bot_id, tenant_id, db)
    return DisclosureConfigResponse(**data)


@bots_router.put("/{bot_id}/disclosure", response_model=DisclosureConfigResponse)
def put_bot_disclosure(
    bot_id: uuid.UUID,
    body: DisclosureConfigUpdate,
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> DisclosureConfigResponse:
    data = bots_service.update_bot_disclosure_config(bot_id, tenant_id, body.level, db)
    return DisclosureConfigResponse(**data)
