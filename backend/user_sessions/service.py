from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import UserSession

_TRACKED_IDENTITY_FIELDS = (
    "email",
    "name",
    "plan_tier",
    "audience_tag",
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_user_id(user_context: dict[str, Any] | None) -> str | None:
    if not user_context:
        return None
    return _clean_optional_text(user_context.get("user_id"))


def _apply_identity_fields(row: UserSession, user_context: dict[str, Any]) -> None:
    """Patch best-known identity fields without clearing prior non-empty values.

    Missing keys in a fresh payload do not erase previously stored values. This keeps the
    latest known profile stable across partial KYC payloads until we introduce an explicit
    "clear" contract for identity fields.
    """
    for key in _TRACKED_IDENTITY_FIELDS:
        value = _clean_optional_text(user_context.get(key))
        if value is not None:
            setattr(row, key, value)


def get_active_user_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: str,
) -> UserSession | None:
    return (
        db.query(UserSession)
        .filter(
            UserSession.tenant_id == tenant_id,
            UserSession.user_id == user_id,
            UserSession.session_ended_at.is_(None),
        )
        .order_by(UserSession.session_started_at.desc())
        .first()
    )


def _close_active_user_sessions(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: str,
    ended_at: datetime,
) -> None:
    rows = (
        db.query(UserSession)
        .filter(
            UserSession.tenant_id == tenant_id,
            UserSession.user_id == user_id,
            UserSession.session_ended_at.is_(None),
        )
        .all()
    )
    if len(rows) > 1:
        logger.warning(
            "multiple_active_user_sessions_detected: tenant_id=%s user_id=%s count=%s",
            tenant_id,
            user_id,
            len(rows),
        )
    for row in rows:
        row.session_ended_at = ended_at
        db.add(row)


def _create_user_session_row(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: str,
    user_context: dict[str, Any] | None,
    started_at: datetime,
) -> UserSession:
    row = UserSession(
        tenant_id=tenant_id,
        user_id=user_id,
        session_started_at=started_at,
        conversation_turns=0,
    )
    _apply_identity_fields(row, user_context or {})
    db.add(row)
    db.flush()
    return row


def start_user_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_context: dict[str, Any] | None,
    started_at: datetime | None = None,
) -> UserSession | None:
    """Start a new active session for ``(tenant_id, user_id)``.

    Closes any existing active sessions and inserts a new row.

    Concurrency: the partial unique index
    ``uq_user_sessions_client_user_active`` prevents two active rows per
    ``(tenant_id, user_id)``. Insert conflicts are isolated behind a
    SAVEPOINT and resolved by returning the row created by the winner.

    Thread safety: callers must own the SQLAlchemy Session and must not
    share one Session across threads. Use a fresh ``SessionLocal`` per
    concurrent caller.
    """
    user_id = _extract_user_id(user_context)
    if not user_id:
        return None
    started = started_at or _now_utc()
    try:
        with db.begin_nested():
            _close_active_user_sessions(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                ended_at=started,
            )
            return _create_user_session_row(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                user_context=user_context,
                started_at=started,
            )
    except IntegrityError:
        logger.info(
            "user_session_start_race_recovered: tenant_id=%s user_id=%s",
            tenant_id,
            user_id,
        )
        return get_active_user_session(db, tenant_id=tenant_id, user_id=user_id)


def touch_user_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_context: dict[str, Any] | None,
    started_at: datetime | None = None,
) -> UserSession | None:
    user_id = _extract_user_id(user_context)
    if not user_id:
        return None
    row = get_active_user_session(db, tenant_id=tenant_id, user_id=user_id)
    if row is None:
        return start_user_session(
            db,
            tenant_id=tenant_id,
            user_context=user_context,
            started_at=started_at,
        )
    _apply_identity_fields(row, user_context or {})
    db.add(row)
    db.flush()
    return row


def record_user_session_turn(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_context: dict[str, Any] | None,
    ended_at: datetime | None = None,
) -> UserSession | None:
    user_id = _extract_user_id(user_context)
    if not user_id:
        return None
    if ended_at is not None:
        row = get_active_user_session(db, tenant_id=tenant_id, user_id=user_id)
        if row is None:
            return None
        _apply_identity_fields(row, user_context or {})
    else:
        row = touch_user_session(db, tenant_id=tenant_id, user_context=user_context)
    if row is None:
        return None
    row.conversation_turns = int(row.conversation_turns or 0) + 1
    if ended_at is not None:
        row.session_ended_at = ended_at
    db.add(row)
    db.flush()
    return row


def sync_user_session_identity(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_context: dict[str, Any] | None,
) -> UserSession | None:
    return touch_user_session(db, tenant_id=tenant_id, user_context=user_context)
