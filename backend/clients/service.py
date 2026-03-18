"""Business logic for client management."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.crypto import encrypt_value
from backend.models import Client


def create_client(user_id: uuid.UUID, name: str, db: Session) -> Client:
    """
    Create a client for a user.

    Generates 32-char random API key. Raises 409 if user already has a client.
    """
    existing = get_client_by_user(user_id, db)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Client already exists for this user",
        )
    api_key = secrets.token_hex(16)  # 32 chars
    client = Client(
        user_id=user_id,
        name=name,
        api_key=api_key,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def get_client_by_user(user_id: uuid.UUID, db: Session) -> Client | None:
    """Get client by user_id. Returns None if not found."""
    return db.query(Client).filter(Client.user_id == user_id).first()


def get_client_by_id(
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> Client:
    """
    Get client by id. Verifies ownership (client.user_id == user_id).
    Raises 404 if not found or not owner.
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client or client.user_id != user_id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def get_client_by_api_key(api_key: str, db: Session) -> Client | None:
    """Get client by API key. Used by widget/chat to validate API key."""
    return db.query(Client).filter(Client.api_key == api_key).first()


def update_client(
    user_id: uuid.UUID,
    db: Session,
    **kwargs: Any,
) -> Client:
    """
    Update current user's client.

    Only updates fields present in kwargs.
    openai_api_key=None means remove the key.
    Raises 404 if no client for user.
    """
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if "name" in kwargs:
        client.name = kwargs["name"]
    if "openai_api_key" in kwargs:
        raw_key = kwargs["openai_api_key"]
        if not raw_key or (isinstance(raw_key, str) and not raw_key.strip()):
            client.openai_api_key = None
        else:
            client.openai_api_key = encrypt_value(raw_key.strip())
    db.commit()
    db.refresh(client)
    return client


def delete_client(
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Delete client. Verifies ownership before delete.
    CASCADE deletes all related documents/chats (already in DB schema).
    Raises 404 if not found or not owner.
    """
    client = get_client_by_id(client_id, user_id, db)
    db.delete(client)
    db.commit()
