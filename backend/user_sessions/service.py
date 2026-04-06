from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import UserSession

_TRACKED_IDENTITY_FIELDS = (
    "email",
    "name",
    "plan_tier",
    "audience_tag",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
    for key in _TRACKED_IDENTITY_FIELDS:
        value = _clean_optional_text(user_context.get(key))
        if value is not None:
            setattr(row, key, value)


def get_active_user_session(
    db: Session,
    *,
    client_id: Any,
    user_id: str,
) -> UserSession | None:
    return (
        db.query(UserSession)
        .filter(
            UserSession.client_id == client_id,
            UserSession.user_id == user_id,
            UserSession.session_ended_at.is_(None),
        )
        .order_by(UserSession.session_started_at.desc())
        .first()
    )


def _close_active_user_sessions(
    db: Session,
    *,
    client_id: Any,
    user_id: str,
    ended_at: datetime,
) -> None:
    rows = (
        db.query(UserSession)
        .filter(
            UserSession.client_id == client_id,
            UserSession.user_id == user_id,
            UserSession.session_ended_at.is_(None),
        )
        .all()
    )
    for row in rows:
        row.session_ended_at = ended_at
        db.add(row)


def start_user_session(
    db: Session,
    *,
    client_id: Any,
    user_context: dict[str, Any] | None,
    started_at: datetime | None = None,
) -> UserSession | None:
    user_id = _extract_user_id(user_context)
    if not user_id:
        return None
    started = started_at or _now_utc()
    _close_active_user_sessions(
        db,
        client_id=client_id,
        user_id=user_id,
        ended_at=started,
    )
    row = UserSession(
        client_id=client_id,
        user_id=user_id,
        session_started_at=started,
        conversation_turns=0,
    )
    _apply_identity_fields(row, user_context or {})
    db.add(row)
    db.flush()
    return row


def touch_user_session(
    db: Session,
    *,
    client_id: Any,
    user_context: dict[str, Any] | None,
    started_at: datetime | None = None,
) -> UserSession | None:
    user_id = _extract_user_id(user_context)
    if not user_id:
        return None
    row = get_active_user_session(db, client_id=client_id, user_id=user_id)
    if row is None:
        return start_user_session(
            db,
            client_id=client_id,
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
    client_id: Any,
    user_context: dict[str, Any] | None,
    ended_at: datetime | None = None,
) -> UserSession | None:
    user_id = _extract_user_id(user_context)
    if not user_id:
        return None
    if ended_at is not None:
        row = get_active_user_session(db, client_id=client_id, user_id=user_id)
        if row is None:
            return None
        _apply_identity_fields(row, user_context or {})
    else:
        row = touch_user_session(db, client_id=client_id, user_context=user_context)
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
    client_id: Any,
    user_context: dict[str, Any] | None,
) -> UserSession | None:
    row = touch_user_session(db, client_id=client_id, user_context=user_context)
    if row is None:
        return None
    db.add(row)
    db.flush()
    return row
