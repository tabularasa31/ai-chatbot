from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)
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


def _enrich_bot_instructions(bot_id: uuid.UUID, tenant_id: uuid.UUID, website_url: str, api_key: str) -> None:
    """Background task: extract company description and update bot agent_instructions."""
    from backend.chat.presets import PRESET_SUPPORT_AGENT
    from backend.core.db import SessionLocal
    from backend.onboarding.extractor import extract_company_description

    description = extract_company_description(website_url, api_key)
    if not description:
        return
    instructions = f"{description}\n\n{PRESET_SUPPORT_AGENT}"
    with SessionLocal() as db:
        bots_service.update_bot(
            bot_id,
            tenant_id,
            db,
            BotUpdate(agent_instructions=instructions),
        )


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
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BotResponse:
    tenant_id: uuid.UUID = current_user.tenant_id  # type: ignore[assignment]
    bot = bots_service.create_bot(
        tenant_id,
        body.name,
        db,
        agent_instructions=body.agent_instructions,
        link_safety_enabled=body.link_safety_enabled,
        allowed_domains=body.allowed_domains,
    )

    tenant = db.get(Tenant, tenant_id)
    try:
        capture_event(
            "bot.created",
            distinct_id=str(bot.public_id),
            tenant_id=str(tenant.public_id) if tenant else None,
            bot_id=str(bot.public_id),
        )
    except Exception:
        pass

    if body.website_url and body.agent_instructions is None:
        if tenant and tenant.openai_api_key:
            try:
                api_key = decrypt_value(tenant.openai_api_key)
                background_tasks.add_task(
                    _enrich_bot_instructions, bot.id, tenant_id, body.website_url, api_key
                )
            except Exception:
                logger.warning("create_bot: could not schedule instruction enrichment", exc_info=True)

    return bot


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
    return bots_service.update_bot(bot_id, tenant_id, db, body)


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
