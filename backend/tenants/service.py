"""Business logic for tenant management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.crypto import encrypt_value
from backend.core.rls import set_tenant_context
from backend.models import Bot, Tenant, TenantPlan, TenantProfile, User
from backend.privacy_config import public_redaction_config_dict, with_redaction_config
from backend.support_config import public_support_config_dict, with_support_config
from backend.tenants.api_keys_service import (
    create_initial_api_key,
    find_active_tenant_by_plain_key,
    get_primary_active_key,
)
from backend.tenants.cache import invalidate_tenant

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
        # Eager-create the knowledge profile so GET /knowledge/profile
        # stays a pure read (no lazy-create side effects on GET).
        db.add(TenantProfile(tenant_id=tenant.id))
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
    if result is None:
        return None
    tenant = result[0]
    set_tenant_context(db, tenant.id)
    return tenant


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
    invalidate_tenant(tenant.id)
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
    invalidate_tenant(tenant.id)
    return get_support_settings_for_user(user_id, db)


def get_plan_for_user(user_id: uuid.UUID, db: Session) -> str:
    """Return the tenant's current tier. Readable by any member.

    Any value the enum does not recognise — ``NULL``, an empty string, a
    tier some later version wrote and this one has never heard of — reads as
    ``free``, the tier that is exactly today's behaviour. The entitlement
    therefore fails closed: a value we cannot interpret can never hand out
    access nobody asked for.

    This is not a guard against the column being *absent*. ``plan`` is a
    mapped column, so the ORM names it in every ``SELECT`` against
    ``tenants`` and a database that predates the migration raises
    ``UndefinedColumn`` before any instance exists to normalise.
    """
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return _normalized_plan(tenant)


def set_plan_for_user(
    user_id: uuid.UUID, plan: str, db: Session
) -> tuple[str, str]:
    """Switch the tenant's tier. Owner-only.

    Returns ``(previous_plan, new_plan)``. The two are equal when the request
    asked for the tier the tenant was already on: the endpoint still succeeds
    (a PUT is idempotent), but the caller can tell a real switch from a no-op
    and avoid reporting a change that did not happen.

    Nothing is charged and nothing is recorded as charged: this flips one
    column. The role model that would let this narrow to a dedicated
    permission is landing separately, so the guard here is the existing owner
    check.
    """
    tenant = get_tenant_by_user(user_id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or getattr(user, "role", None) != "owner":
        raise HTTPException(status_code=403, detail="Owner role required")
    try:
        target = TenantPlan(plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unknown plan") from exc
    previous = _normalized_plan(tenant)
    tenant.plan = target.value
    db.commit()
    db.refresh(tenant)
    invalidate_tenant(tenant.id)
    return previous, _normalized_plan(tenant)


def _normalized_plan(tenant: Tenant) -> str:
    """Coerce a stored tier to a known one, falling back to ``free``.

    See ``get_plan_for_user`` for why the fallback is ``free`` and what it
    does not cover.
    """
    try:
        return TenantPlan(tenant.plan).value
    except (ValueError, TypeError):
        # TypeError as well as ValueError: enum lookup hashes the value, so a
        # non-hashable one raises the other exception. Either way the tier is
        # uninterpretable and must fall back.
        return TenantPlan.free.value


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
    invalidate_tenant(tenant.id)
    return tenant




def delete_tenant(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Delete tenant and its members. Verifies ownership before delete.
    CASCADE deletes all related documents/chats (already in DB schema).
    Raises 404 if not found or not owner.
    """
    tenant = get_tenant_by_id(tenant_id, user_id, db, require_owner=True)
    # Members go with the workspace. ``users.tenant_id`` is ON DELETE SET NULL,
    # so without this every member survives as an account belonging to nothing
    # — the exact orphan that removing a member was changed to avoid, and worse
    # here because it also burns the address: inviting them elsewhere answers
    # "already registered to another workspace" when they belong to none, and
    # /auth/register refuses the address too. Nobody could free it but us.
    #
    # No attribution stamping, unlike ``remove_member``: everything a label
    # would preserve — messages, operator stretches, gap dismissals, API keys —
    # is scoped to this tenant and cascades away with it, so there is no
    # surviving history left to sign.
    members = db.query(User).filter(User.tenant_id == tenant_id).all()
    for member in members:
        db.delete(member)
    db.flush()
    db.delete(tenant)
    db.commit()
    invalidate_tenant(tenant_id)
