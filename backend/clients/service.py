"""Business logic for client management."""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.crypto import decrypt_value, encrypt_value
from backend.disclosure_config import ALLOWED_LEVELS, public_config_dict
from backend.privacy_config import public_redaction_config_dict, with_redaction_config
from backend.support_config import public_support_config_dict, with_support_config
from backend.models import Chat, Client, User

DEFAULT_CLIENT_NAME = "My Workspace"


def _dt_utc_aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


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
    try:
        db.flush()
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.client_id = client.id
        db.commit()
        db.refresh(client)
    except IntegrityError as exc:
        db.rollback()
        if get_client_by_user(user_id, db):
            raise HTTPException(
                status_code=409,
                detail="Client already exists for this user",
            ) from exc
        raise

    return client


def ensure_client_for_user(
    user_id: uuid.UUID,
    db: Session,
    name: str = DEFAULT_CLIENT_NAME,
) -> Client:
    """Return the user's client, creating it if needed."""
    client = get_client_by_user(user_id, db)
    if client:
        return client
    try:
        return create_client(user_id, name, db)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        client = get_client_by_user(user_id, db)
        if client:
            return client
        raise


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


def get_disclosure_config_for_user(user_id: uuid.UUID, db: Session) -> dict[str, str]:
    """Return canonical {\"level\": ...} for the current user's client (defaults if unset)."""
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    raw = client.disclosure_config if isinstance(client.disclosure_config, dict) else None
    return public_config_dict(raw)


def update_disclosure_config_for_user(
    user_id: uuid.UUID,
    level: str,
    db: Session,
) -> dict[str, str]:
    """Validate level, persist {\"level\": ...}, return canonical config."""
    if level not in ALLOWED_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"level must be one of: {', '.join(sorted(ALLOWED_LEVELS))}",
        )
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client.disclosure_config = {"level": level}
    db.commit()
    db.refresh(client)
    return public_config_dict(client.disclosure_config)


def get_redaction_config_for_user(user_id: uuid.UUID, db: Session) -> dict[str, list[str]]:
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    raw = client.settings if isinstance(client.settings, dict) else None
    return public_redaction_config_dict(raw)


def update_redaction_config_for_user(
    user_id: uuid.UUID,
    optional_entity_types: list[str],
    db: Session,
) -> dict[str, list[str]]:
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    config = {"optional_entity_types": sorted(set(optional_entity_types))}
    client.settings = with_redaction_config(client.settings if isinstance(client.settings, dict) else None, config)
    db.commit()
    db.refresh(client)
    return public_redaction_config_dict(client.settings if isinstance(client.settings, dict) else None)


def get_support_settings_for_user(user_id: uuid.UUID, db: Session) -> dict[str, str | None]:
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    owner = db.query(User).filter(User.id == client.user_id).first()
    raw = client.settings if isinstance(client.settings, dict) else None
    config = public_support_config_dict(raw)
    return {
        "l2_email": config["l2_email"],
        "fallback_email": owner.email if owner and owner.email else None,
    }


def update_support_settings_for_user(
    user_id: uuid.UUID,
    l2_email: str | None,
    db: Session,
) -> dict[str, str | None]:
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client.settings = with_support_config(
        client.settings if isinstance(client.settings, dict) else None,
        {"l2_email": l2_email},
    )
    db.commit()
    db.refresh(client)
    return get_support_settings_for_user(user_id, db)


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


def _decrypt_kyc_secret(enc: str | None) -> str | None:
    if not enc:
        return None
    try:
        return decrypt_value(enc)
    except RuntimeError:
        return None


def generate_kyc_secret_for_client(user_id: uuid.UUID, db: Session) -> tuple[Client, str]:
    """
    Create a new KYC signing secret (first time only). Returns (client, plaintext_secret_once).
    Raises 404 if no client, 409 if secret already exists.
    """
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.kyc_secret_key:
        raise HTTPException(
            status_code=409,
            detail="KYC signing secret already exists; use rotate to replace it",
        )
    raw = secrets.token_hex(32)
    client.kyc_secret_key = encrypt_value(raw)
    client.kyc_secret_key_hint = raw[-4:]
    db.commit()
    db.refresh(client)
    return client, raw


def rotate_kyc_secret(user_id: uuid.UUID, db: Session) -> tuple[Client, str]:
    """
    Rotate KYC secret; previous ciphertext kept until overlap window expires.
    """
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if not client.kyc_secret_key:
        raise HTTPException(
            status_code=400,
            detail="No KYC signing secret configured; generate one first",
        )
    overlap_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    client.kyc_secret_key_previous = client.kyc_secret_key
    client.kyc_secret_previous_expires_at = overlap_until
    raw = secrets.token_hex(32)
    client.kyc_secret_key = encrypt_value(raw)
    client.kyc_secret_key_hint = raw[-4:]
    db.commit()
    db.refresh(client)
    return client, raw


def get_kyc_status(user_id: uuid.UUID, db: Session) -> dict[str, Any]:
    client = get_client_by_user(user_id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    now = dt.datetime.now(dt.timezone.utc)
    week_ago = now - dt.timedelta(days=7)

    total_7d = (
        db.query(func.count(Chat.id))
        .filter(Chat.client_id == client.id, Chat.created_at >= week_ago)
        .scalar()
    ) or 0
    identified_7d = (
        db.query(func.count(Chat.id))
        .filter(
            Chat.client_id == client.id,
            Chat.created_at >= week_ago,
            Chat.user_context.isnot(None),
        )
        .scalar()
    ) or 0

    rate = (identified_7d / total_7d) if total_7d else 0.0

    last_row = (
        db.query(Chat)
        .filter(Chat.client_id == client.id, Chat.user_context.isnot(None))
        .order_by(Chat.created_at.desc())
        .first()
    )
    last_identified = last_row.created_at if last_row else None

    return {
        "has_secret": bool(client.kyc_secret_key),
        "identified_session_rate_7d": rate,
        "last_identified_session": last_identified,
        "masked_secret_hint": _masked_kyc_hint(client),
    }


def _masked_kyc_hint(client: Client) -> str | None:
    if not client.kyc_secret_key or not client.kyc_secret_key_hint:
        return None
    return f"••••••••••••••••••••••••••••••••...{client.kyc_secret_key_hint}"


def get_kyc_decrypted_keys_for_validation(client: Client) -> list[tuple[str, str | None]]:
    """
    Return list of (plaintext_secret, label) to try when validating a token.
    label is None for current, 'previous' for overlap key.
    """
    keys: list[tuple[str, str | None]] = []
    cur = _decrypt_kyc_secret(client.kyc_secret_key)
    if cur:
        keys.append((cur, None))
    now = dt.datetime.now(dt.timezone.utc)
    prev_exp = _dt_utc_aware(client.kyc_secret_previous_expires_at)
    if client.kyc_secret_key_previous and prev_exp and prev_exp > now:
        prev = _decrypt_kyc_secret(client.kyc_secret_key_previous)
        if prev:
            keys.append((prev, "previous"))
    return keys


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
