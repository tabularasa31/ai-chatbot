"""Business logic for tenant management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.crypto import encrypt_value
from backend.models import Bot, Tenant, User
from backend.privacy_config import public_redaction_config_dict, with_redaction_config
from backend.support_config import public_support_config_dict, with_support_config
from backend.tenants.api_keys_service import (
    create_initial_api_key,
    find_active_tenant_by_plain_key,
    get_primary_active_key,
)

DEFAULT_TENANT_NAME = "My Workspace"


def create_tenant(
    user_id: uuid.UUID, name: str, db: Session
) -> tuple[Tenant, str]:
    """
    Create a tenant for a user.

    Generates the initial widget API key (ck_-prefixed) and returns it
    as plaintext alongside the tenant — this is the only point where the
    plaintext is ever surfaced. Raises 409 if user already has a tenant.
    """
    existing = get_tenant_by_user(user_id, db)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Tenant already exists for this user",
        )
    tenant = Tenant(name=name)
    db.add(tenant)
    try:
        db.flush()
        plaintext_key = create_initial_api_key(
            tenant.id, db, created_by_user_id=user_id
        )
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.tenant_id = tenant.id
        db.add(Bot(tenant_id=tenant.id, name=name))
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

    return tenant, plaintext_key


def ensure_tenant_for_user(
    user_id: uuid.UUID,
    db: Session,
    name: str = DEFAULT_TENANT_NAME,
) -> Tenant:
    """Return the user's tenant, creating it if needed.

    The plaintext widget key generated on creation is intentionally
    discarded here — callers that need it must use ``create_tenant``
    directly.
    """
    tenant = get_tenant_by_user(user_id, db)
    if tenant:
        return tenant
    try:
        tenant, _plain = create_tenant(user_id, name, db)
        return tenant
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        tenant = get_tenant_by_user(user_id, db)
        if tenant:
            return tenant
        raise


def get_tenant_by_user(user_id: uuid.UUID, db: Session) -> Tenant | None:
    """Get the tenant the user belongs to (single JOIN query)."""
    return (
        db.query(Tenant)
        .join(User, User.tenant_id == Tenant.id)
        .filter(User.id == user_id)
        .first()
    )


def get_tenant_by_id(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
    *,
    require_owner: bool = False,
) -> Tenant:
    """
    Get tenant by id. Verifies the user belongs to this tenant.
    Raises 404 if not found or not a member.
    Pass require_owner=True for destructive operations (delete, rotate keys).
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if not tenant or not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if require_owner and user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner role required")
    return tenant


def get_tenant_by_api_key(api_key: str, db: Session) -> Tenant | None:
    """Resolve a tenant by a plaintext widget API key.

    Lookup goes through tenant_api_keys by hash; revoked or expired keys
    return ``None``. Used by /widget endpoints and the X-API-Key header
    on /chat.
    """
    result = find_active_tenant_by_plain_key(api_key, db)
    return None if result is None else result[0]


def get_primary_api_key_hint(tenant_id: uuid.UUID, db: Session) -> str | None:
    """Last 4 chars of the tenant's primary active key, for UI display."""
    row = get_primary_active_key(tenant_id, db)
    return row.key_hint if row else None


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
    owner = db.query(User).filter(User.tenant_id == tenant.id, User.role == "owner").limit(1).first()
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
    tenant = get_tenant_by_id(tenant_id, user_id, db, require_owner=True)
    db.delete(tenant)
    db.commit()
