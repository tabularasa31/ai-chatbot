from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.bots.schemas import BotUpdate
from backend.chat.presets import PRESET_SUPPORT_AGENT
from backend.disclosure_config import ALLOWED_LEVELS, public_config_dict
from backend.models import Bot


def get_bots_for_tenant(tenant_id: uuid.UUID, db: Session) -> list[Bot]:
    return db.query(Bot).filter(Bot.tenant_id == tenant_id).order_by(Bot.created_at.asc()).all()


def get_bot_by_id(bot_id: uuid.UUID, tenant_id: uuid.UUID, db: Session) -> Bot:
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.tenant_id == tenant_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


def get_bot_by_public_id(public_id: str, db: Session) -> Bot | None:
    return db.query(Bot).filter(Bot.public_id == public_id).first()


def create_bot(
    tenant_id: uuid.UUID,
    name: str,
    db: Session,
    *,
    agent_instructions: str | None = None,
) -> Bot:
    bot = Bot(
        tenant_id=tenant_id,
        name=name,
        agent_instructions=agent_instructions if agent_instructions is not None else PRESET_SUPPORT_AGENT,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot


def update_bot(
    bot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
    update: BotUpdate,
) -> Bot:
    bot = get_bot_by_id(bot_id, tenant_id, db)
    fields = update.model_fields_set

    if "is_active" in fields and update.is_active is False and bot.is_active:
        active_count = (
            db.query(Bot)
            .filter(Bot.tenant_id == tenant_id, Bot.is_active.is_(True))
            .with_for_update()
            .count()
        )
        if active_count <= 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot deactivate the last active bot of a tenant",
            )

    if "name" in fields:
        bot.name = update.name  # type: ignore[assignment]
    if "is_active" in fields:
        bot.is_active = update.is_active  # type: ignore[assignment]
    if "agent_instructions" in fields:
        bot.agent_instructions = update.agent_instructions  # None clears the field

    db.commit()
    db.refresh(bot)
    return bot


def get_bot_disclosure_config(bot_id: uuid.UUID, tenant_id: uuid.UUID, db: Session) -> dict[str, str]:
    bot = get_bot_by_id(bot_id, tenant_id, db)
    raw = bot.disclosure_config if isinstance(bot.disclosure_config, dict) else None
    return public_config_dict(raw)


def update_bot_disclosure_config(
    bot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    level: str,
    db: Session,
) -> dict[str, str]:
    if level not in ALLOWED_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"level must be one of: {', '.join(sorted(ALLOWED_LEVELS))}",
        )
    bot = get_bot_by_id(bot_id, tenant_id, db)
    bot.disclosure_config = {"level": level}
    db.commit()
    db.refresh(bot)
    return public_config_dict(bot.disclosure_config)


_MIN_BOTS_PER_TENANT = 1


def delete_bot(bot_id: uuid.UUID, tenant_id: uuid.UUID, db: Session) -> None:
    bots = (
        db.query(Bot)
        .filter(Bot.tenant_id == tenant_id)
        .with_for_update()
        .all()
    )
    if len(bots) <= _MIN_BOTS_PER_TENANT:
        raise HTTPException(status_code=409, detail="Cannot delete the last bot of a tenant")
    target = next((b for b in bots if b.id == bot_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    db.delete(target)
    db.commit()
