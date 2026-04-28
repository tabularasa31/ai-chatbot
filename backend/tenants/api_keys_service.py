"""Service layer for rotating and revoking widget API keys.

The widget client authenticates with a plaintext ``ck_…`` key. The
plaintext is never stored — only its SHA-256 hash plus the last 4 chars
(``key_hint``) for UI identification. Each tenant may hold one ACTIVE
key plus, briefly, one REVOKING key during a grace window so the
embedded widget keeps working while the customer rolls out the new key.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.utils import generate_api_key
from backend.models import Tenant, TenantApiKey, User
from backend.models.tenant import (
    TENANT_API_KEY_REASONS,
    TENANT_API_KEY_STATUS_ACTIVE,
    TENANT_API_KEY_STATUS_REVOKED,
    TENANT_API_KEY_STATUS_REVOKING,
)

logger = logging.getLogger(__name__)

DEFAULT_GRACE_HOURS = 24
RotationReason = Literal["leaked", "scheduled", "compromise", "other"]


def hash_api_key(plain: str) -> str:
    """Stable SHA-256 hex digest used both for storage and lookup."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _aware_utc(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=dt.UTC)
    return d.astimezone(dt.UTC)


def _create_key_row(
    tenant_id: uuid.UUID,
    db: Session,
    *,
    created_by_user_id: uuid.UUID | None = None,
) -> tuple[TenantApiKey, str]:
    """Generate a fresh ck_ key, persist its hash, return (row, plaintext)."""
    plain = generate_api_key()
    row = TenantApiKey(
        tenant_id=tenant_id,
        key_hash=hash_api_key(plain),
        key_hint=plain[-4:],
        status=TENANT_API_KEY_STATUS_ACTIVE,
        created_by_user_id=created_by_user_id,
    )
    db.add(row)
    return row, plain


def create_initial_api_key(
    tenant_id: uuid.UUID,
    db: Session,
    *,
    created_by_user_id: uuid.UUID | None = None,
) -> str:
    """Insert the first ACTIVE key for a freshly-created tenant.

    Caller is responsible for committing the surrounding transaction.
    """
    _, plain = _create_key_row(tenant_id, db, created_by_user_id=created_by_user_id)
    return plain


def list_api_keys(tenant_id: uuid.UUID, db: Session) -> list[TenantApiKey]:
    """Return all keys for a tenant, newest first.

    Includes already-revoked keys so the audit trail is visible in the UI.
    """
    return (
        db.query(TenantApiKey)
        .filter(TenantApiKey.tenant_id == tenant_id)
        .order_by(TenantApiKey.created_at.desc())
        .all()
    )


def find_active_tenant_by_plain_key(
    plain: str, db: Session
) -> tuple[Tenant, TenantApiKey] | None:
    """Look up a tenant by a plaintext widget key.

    Returns ``None`` if no matching key exists, the key is fully revoked,
    or its grace window has expired. Touches ``last_used_at`` on a hit
    but does not commit — the caller's request transaction will flush it.
    """
    if not plain:
        return None
    digest = hash_api_key(plain.strip())
    row = db.query(TenantApiKey).filter(TenantApiKey.key_hash == digest).first()
    if not row:
        return None
    now = dt.datetime.now(dt.UTC)
    if row.status == TENANT_API_KEY_STATUS_REVOKED:
        return None
    if row.status == TENANT_API_KEY_STATUS_REVOKING:
        exp = _aware_utc(row.expires_at)
        if exp is None or exp <= now:
            return None
    tenant = db.query(Tenant).filter(Tenant.id == row.tenant_id).first()
    if not tenant:
        return None
    row.last_used_at = now.replace(tzinfo=None)
    return tenant, row


def rotate_api_key(
    tenant_id: uuid.UUID,
    db: Session,
    *,
    reason: RotationReason,
    revoke_old_immediately: bool = False,
    grace_hours: int = DEFAULT_GRACE_HOURS,
    actor_user_id: uuid.UUID | None = None,
) -> tuple[TenantApiKey, str]:
    """Issue a new ACTIVE key and put existing ACTIVE keys into REVOKING.

    Returns ``(new_row, plaintext)``. Plaintext must be surfaced to the
    caller exactly once.
    """
    if reason not in TENANT_API_KEY_REASONS:
        raise HTTPException(status_code=400, detail="Invalid rotation reason")

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    expires_at = (
        None
        if revoke_old_immediately
        else now + dt.timedelta(hours=max(1, grace_hours))
    )

    existing_active = (
        db.query(TenantApiKey)
        .filter(
            TenantApiKey.tenant_id == tenant_id,
            TenantApiKey.status == TENANT_API_KEY_STATUS_ACTIVE,
        )
        .all()
    )
    for row in existing_active:
        if revoke_old_immediately:
            row.status = TENANT_API_KEY_STATUS_REVOKED
            row.revoked_at = now
            row.revoked_reason = reason
            row.expires_at = now
        else:
            row.status = TENANT_API_KEY_STATUS_REVOKING
            row.revoked_reason = reason
            row.expires_at = expires_at

    # Also collapse any lingering REVOKING rows when an immediate revoke is
    # requested — explicit kill-switch should leave nothing usable.
    if revoke_old_immediately:
        revoking_rows = (
            db.query(TenantApiKey)
            .filter(
                TenantApiKey.tenant_id == tenant_id,
                TenantApiKey.status == TENANT_API_KEY_STATUS_REVOKING,
            )
            .all()
        )
        for row in revoking_rows:
            row.status = TENANT_API_KEY_STATUS_REVOKED
            row.revoked_at = now
            row.expires_at = now

    new_row, plain = _create_key_row(
        tenant_id, db, created_by_user_id=actor_user_id
    )
    db.flush()
    db.commit()
    db.refresh(new_row)

    logger.info(
        "tenant_api_key_rotated",
        extra={
            "tenant_id": str(tenant_id),
            "new_key_id": str(new_row.id),
            "reason": reason,
            "revoke_old_immediately": revoke_old_immediately,
            "grace_hours": 0 if revoke_old_immediately else grace_hours,
        },
    )
    return new_row, plain


def revoke_api_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    db: Session,
    *,
    reason: RotationReason = "compromise",
) -> TenantApiKey:
    """Immediately mark a single key as REVOKED.

    Refuses to revoke the last remaining usable key — the tenant would
    lose all widget access with no replacement. The caller should
    rotate first in that scenario.
    """
    row = (
        db.query(TenantApiKey)
        .filter(TenantApiKey.id == key_id, TenantApiKey.tenant_id == tenant_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.status == TENANT_API_KEY_STATUS_REVOKED:
        return row

    other_usable = (
        db.query(TenantApiKey)
        .filter(
            TenantApiKey.tenant_id == tenant_id,
            TenantApiKey.id != key_id,
            TenantApiKey.status == TENANT_API_KEY_STATUS_ACTIVE,
        )
        .count()
    )
    if other_usable == 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot revoke the only active key; rotate first.",
        )

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    row.status = TENANT_API_KEY_STATUS_REVOKED
    row.revoked_at = now
    row.revoked_reason = reason
    row.expires_at = now
    db.commit()
    db.refresh(row)

    logger.info(
        "tenant_api_key_revoked",
        extra={
            "tenant_id": str(tenant_id),
            "key_id": str(key_id),
            "reason": reason,
        },
    )
    return row


def get_primary_active_key(
    tenant_id: uuid.UUID, db: Session
) -> TenantApiKey | None:
    """Return the newest ACTIVE key for a tenant, or ``None`` if there is
    no usable key (should not happen for a healthy tenant)."""
    return (
        db.query(TenantApiKey)
        .filter(
            TenantApiKey.tenant_id == tenant_id,
            TenantApiKey.status == TENANT_API_KEY_STATUS_ACTIVE,
        )
        .order_by(TenantApiKey.created_at.desc())
        .first()
    )


def assert_owner(user: User, tenant_id: uuid.UUID) -> None:
    """Owner-only guard for destructive key operations."""
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if getattr(user, "role", None) != "owner":
        raise HTTPException(status_code=403, detail="Owner role required")
