from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.bots import service as bots_service
from backend.bots.schemas import BotCreate, BotList, BotResponse, BotUpdate
from backend.core.db import get_db
from backend.models import User
from backend.tenants.service import get_tenant_by_user

bots_router = APIRouter(prefix="/bots", tags=["bots"])


def _tenant_id(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> uuid.UUID:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.id


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
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> BotResponse:
    return bots_service.create_bot(tenant_id, body.name, db)


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
        bot_id, tenant_id, db, name=body.name, is_active=body.is_active
    )


@bots_router.delete("/{bot_id}", status_code=204, response_model=None)
def delete_bot(
    bot_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(_tenant_id)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    bots_service.delete_bot(bot_id, tenant_id, db)
