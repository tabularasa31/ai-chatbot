from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import Chat

WIDGET_IDENTIFIED_RESUME_TTL_HOURS = 24

_PATCHABLE_USER_CONTEXT_FIELDS = (
    "email",
    "name",
    "plan_tier",
    "audience_tag",
    "company",
    "locale",
)

SESSION_INVALID_CODE = "session_invalid"
SESSION_NOT_FOUND_CODE = "session_not_found"
SESSION_FORBIDDEN_CODE = "session_forbidden"
SESSION_CLOSED_CODE = "session_closed"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def find_resumable_identified_chat(
    db: Session,
    *,
    client_id: Any,
    user_id: str,
    ttl_hours: int = WIDGET_IDENTIFIED_RESUME_TTL_HOURS,
) -> Chat | None:
    """Return the most recent open chat for the identified user within the TTL window."""
    cutoff = _now_utc() - timedelta(hours=ttl_hours)
    target_user_id = user_id.strip()
    if not target_user_id:
        return None

    return (
        db.query(Chat)
        .filter(
            Chat.client_id == client_id,
            Chat.ended_at.is_(None),
            Chat.updated_at >= cutoff,
            Chat.user_context.isnot(None),
            Chat.user_context["user_id"].as_string() == target_user_id,
        )
        .order_by(Chat.updated_at.desc())
        .first()
    )


def apply_identity_context_patch(
    existing_ctx: dict[str, Any] | None,
    fresh_ctx: dict[str, Any],
    *,
    browser_locale: str | None = None,
) -> dict[str, Any]:
    """
    Update only whitelisted identity fields on a resumed chat.

    The existing user_id remains canonical for the resumed session.
    """
    patched = dict(existing_ctx or {})
    for key in _PATCHABLE_USER_CONTEXT_FIELDS:
        value = _clean_optional_text(fresh_ctx.get(key))
        if value is not None:
            patched[key] = value

    locale_value = _clean_optional_text(browser_locale)
    if locale_value is not None:
        patched["browser_locale"] = locale_value

    return patched


def widget_session_error_detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
