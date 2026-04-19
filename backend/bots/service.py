from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

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


def create_bot(tenant_id: uuid.UUID, name: str, db: Session) -> Bot:
    bot = Bot(tenant_id=tenant_id, name=name)
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot


def update_bot(
    bot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> Bot:
    bot = get_bot_by_id(bot_id, tenant_id, db)
    if name is not None:
        bot.name = name
    if is_active is not None:
        bot.is_active = is_active
    db.commit()
    db.refresh(bot)
    return bot


_MIN_BOTS_PER_TENANT = 1


def delete_bot(bot_id: uuid.UUID, tenant_id: uuid.UUID, db: Session) -> None:
    # Lock all bot rows for this tenant before counting to prevent the race
    # condition where two concurrent deletes both pass the count check.
    # with_for_update() is a no-op on SQLite (which serialises writes anyway).
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
