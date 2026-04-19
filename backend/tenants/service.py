"""Business logic for tenant management."""

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
from backend.models import Chat, Tenant, User
from backend.privacy_config import public_redaction_config_dict, with_redaction_config
from backend.support_config import public_support_config_dict, with_support_config

DEFAULT_TENANT_NAME = "My Workspace"


def _dt_utc_aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=dt.UTC)
    return d.astimezone(dt.UTC)


def create_tenant(user_id: uuid.UUID, name: str, db: Session) -> Tenant:
    """
    Create a tenant for a user.

    Generates 32-char random API key. Raises 409 if user already has a tenant.
    """
    existing = get_tenant_by_user(user_id, db)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Tenant already exists for this user",
        )
    api_key = secrets.token_hex(16)  # 32 chars
    tenant = Tenant(
        user_id=user_id,
        name=name,
        api_key=api_key,
    )
    db.add(tenant)
    try:
        db.flush()
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.tenant_id = tenant.id
        db.commit()
        db.refresh(tenant)
    except IntegrityError as exc:
        db.rollback()
        if get_tenant_by_user(user_id, db):
            raise HTTPException(
                status_code=409,
                detail="Tenant already exists for this user",
            ) from exc
        raise

    return tenant


def ensure_tenant_for_user(
    user_id: uuid.UUID,
    db: Session,
    name: str = DEFAULT_TENANT_NAME,
) -> Tenant:
    """Return the user's tenant, creating it if needed."""
    tenant = get_tenant_by_user(user_id, db)
    if tenant:
        return tenant
    try:
        return create_tenant(user_id, name, db)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        tenant = get_tenant_by_user(user_id, db)
        if tenant:
            return tenant
        raise


def get_tenant_by_user(user_id: uuid.UUID, db: Session) -> Tenant | None:
    """Get tenant by user_id. Returns None if not found."""
    return db.query(Tenant).filter(Tenant.user_id == user_id).first()


def get_tenant_by_id(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> Tenant:
    """
    Get tenant by id. Verifies ownership (tenant.user_id == user_id).
    Raises 404 if not found or not owner.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant or tenant.user_id != user_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def get_tenant_by_api_key(api_key: str, db: Session) -> Tenant | None:
    """Get tenant by API key. Used by widget/chat to validate API key."""
    return db.query(Tenant).filter(Tenant.api_key == api_key).first()


def get_disclosure_config_for_user(user_id: uuid.UUID, db: Session) -> dict[str, str]:
    """Return canonical {\"level\": ...} for the current user's tenant (defaults if unset)."""
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    raw = tenant.disclosure_config if isinstance(tenant.disclosure_config, dict) else None
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
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.disclosure_config = {"level": level}
    db.commit()
    db.refresh(tenant)
    return public_config_dict(tenant.disclosure_config)


def get_redaction_config_for_user(user_id: uuid.UUID, db: Session) -> dict[str, list[str]]:
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    raw = tenant.settings if isinstance(tenant.settings, dict) else None
    return public_redaction_config_dict(raw)


def update_redaction_config_for_user(
    user_id: uuid.UUID,
    optional_entity_types: list[str],
    db: Session,
) -> dict[str, list[str]]:
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = {"optional_entity_types": sorted(set(optional_entity_types))}
    tenant.settings = with_redaction_config(tenant.settings if isinstance(tenant.settings, dict) else None, config)
    db.commit()
    db.refresh(tenant)
    return public_redaction_config_dict(tenant.settings if isinstance(tenant.settings, dict) else None)


def get_support_settings_for_user(user_id: uuid.UUID, db: Session) -> dict[str, str | None]:
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    owner = db.query(User).filter(User.id == tenant.user_id).first()
    raw = tenant.settings if isinstance(tenant.settings, dict) else None
    config = public_support_config_dict(raw)
    return {
        "l2_email": config["l2_email"],
        "escalation_language": config["escalation_language"],
        "fallback_email": owner.email if owner and owner.email else None,
    }


def update_support_settings_for_user(
    user_id: uuid.UUID,
    config: dict[str, str | None],
    db: Session,
) -> dict[str, str | None]:
    """Update support settings using only the keys present in *config*.

    Keys absent from *config* are left unchanged, so callers that only know
    about a subset of settings (e.g. older API tenants that predate
    escalation_language) cannot accidentally clear fields they did not touch.
    """
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.settings = with_support_config(
        tenant.settings if isinstance(tenant.settings, dict) else None,
        config,
    )
    db.commit()
    db.refresh(tenant)
    return get_support_settings_for_user(user_id, db)


def update_tenant(
    user_id: uuid.UUID,
    db: Session,
    **kwargs: Any,
) -> Tenant:
    """
    Update current user's tenant.

    Only updates fields present in kwargs.
    openai_api_key=None means remove the key.
    Raises 404 if no tenant for user.
    """
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if "name" in kwargs:
        tenant.name = kwargs["name"]
    if "openai_api_key" in kwargs:
        raw_key = kwargs["openai_api_key"]
        if not raw_key or (isinstance(raw_key, str) and not raw_key.strip()):
            tenant.openai_api_key = None
        else:
            tenant.openai_api_key = encrypt_value(raw_key.strip())
    db.commit()
    db.refresh(tenant)
    return tenant


def _decrypt_kyc_secret(enc: str | None) -> str | None:
    if not enc:
        return None
    try:
        return decrypt_value(enc)
    except RuntimeError:
        return None


def generate_kyc_secret_for_tenant(user_id: uuid.UUID, db: Session) -> tuple[Tenant, str]:
    """
    Create a new KYC signing secret (first time only). Returns (tenant, plaintext_secret_once).
    Raises 404 if no tenant, 409 if secret already exists.
    """
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.kyc_secret_key:
        raise HTTPException(
            status_code=409,
            detail="KYC signing secret already exists; use rotate to replace it",
        )
    raw = secrets.token_hex(32)
    tenant.kyc_secret_key = encrypt_value(raw)
    tenant.kyc_secret_key_hint = raw[-4:]
    db.commit()
    db.refresh(tenant)
    return tenant, raw


def rotate_kyc_secret(user_id: uuid.UUID, db: Session) -> tuple[Tenant, str]:
    """
    Rotate KYC secret; previous ciphertext kept until overlap window expires.
    """
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not tenant.kyc_secret_key:
        raise HTTPException(
            status_code=400,
            detail="No KYC signing secret configured; generate one first",
        )
    overlap_until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    tenant.kyc_secret_key_previous = tenant.kyc_secret_key
    tenant.kyc_secret_previous_expires_at = overlap_until
    raw = secrets.token_hex(32)
    tenant.kyc_secret_key = encrypt_value(raw)
    tenant.kyc_secret_key_hint = raw[-4:]
    db.commit()
    db.refresh(tenant)
    return tenant, raw


def get_kyc_status(user_id: uuid.UUID, db: Session) -> dict[str, Any]:
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = dt.datetime.now(dt.UTC)
    week_ago = now - dt.timedelta(days=7)

    total_7d = (
        db.query(func.count(Chat.id))
        .filter(Chat.tenant_id == tenant.id, Chat.created_at >= week_ago)
        .scalar()
    ) or 0
    identified_7d = (
        db.query(func.count(Chat.id))
        .filter(
            Chat.tenant_id == tenant.id,
            Chat.created_at >= week_ago,
            Chat.user_context.isnot(None),
        )
        .scalar()
    ) or 0

    rate = (identified_7d / total_7d) if total_7d else 0.0

    last_row = (
        db.query(Chat)
        .filter(Chat.tenant_id == tenant.id, Chat.user_context.isnot(None))
        .order_by(Chat.created_at.desc())
        .first()
    )
    last_identified = last_row.created_at if last_row else None

    return {
        "has_secret": bool(tenant.kyc_secret_key),
        "identified_session_rate_7d": rate,
        "last_identified_session": last_identified,
        "masked_secret_hint": _masked_kyc_hint(tenant),
    }


def _masked_kyc_hint(tenant: Tenant) -> str | None:
    if not tenant.kyc_secret_key or not tenant.kyc_secret_key_hint:
        return None
    return f"••••••••••••••••••••••••••••••••...{tenant.kyc_secret_key_hint}"


def get_kyc_decrypted_keys_for_validation(tenant: Tenant) -> list[tuple[str, str | None]]:
    """
    Return list of (plaintext_secret, label) to try when validating a token.
    label is None for current, 'previous' for overlap key.
    """
    keys: list[tuple[str, str | None]] = []
    cur = _decrypt_kyc_secret(tenant.kyc_secret_key)
    if cur:
        keys.append((cur, None))
    now = dt.datetime.now(dt.UTC)
    prev_exp = _dt_utc_aware(tenant.kyc_secret_previous_expires_at)
    if tenant.kyc_secret_key_previous and prev_exp and prev_exp > now:
        prev = _decrypt_kyc_secret(tenant.kyc_secret_key_previous)
        if prev:
            keys.append((prev, "previous"))
    return keys


def delete_tenant(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Delete tenant. Verifies ownership before delete.
    CASCADE deletes all related documents/chats (already in DB schema).
    Raises 404 if not found or not owner.
    """
    tenant = get_tenant_by_id(tenant_id, user_id, db)
    db.delete(tenant)
    db.commit()
