from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_IDENTITY_FIELD_CAPS = {
    "email": 320,
    "name": 200,
    "plan_tier": 64,
    "audience_tag": 64,
    "company": 200,
    "locale": 35,
}
_PATCHABLE_USER_CONTEXT_FIELDS = tuple(_IDENTITY_FIELD_CAPS.keys())
_USER_ID_CAP = 200
_BROWSER_LOCALE_CAP = 35
_BCP47_RE = re.compile(
    r"^[A-Za-z]{2,3}(-[A-Za-z]{4})?(-([A-Za-z]{2}|\d{3}))?"
    r"(-([A-Za-z0-9]{5,8}|\d[A-Za-z0-9]{3}))*$"
)

SESSION_INVALID_CODE = "session_invalid"
SESSION_NOT_FOUND_CODE = "session_not_found"
SESSION_CLOSED_CODE = "session_closed"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _clean_capped_text(value: Any, cap: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > cap:
        logger.info(
            "widget_identity_field_truncated",
            extra={"original_length": len(text), "cap": cap},
        )
        return text[:cap]
    return text


def sanitize_locale(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > _BROWSER_LOCALE_CAP:
        if value:
            logger.info(
                "widget_locale_rejected",
                extra={"value_preview": value[:20]},
            )
        return None
    if not _BCP47_RE.match(value):
        logger.info(
            "widget_locale_rejected",
            extra={"value_preview": value[:20]},
        )
        return None
    return value


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
    patched: dict[str, Any] = {}
    source = existing_ctx or {}

    user_id = _clean_capped_text(source.get("user_id"), _USER_ID_CAP)
    if user_id is not None:
        patched["user_id"] = user_id

    for key, cap in _IDENTITY_FIELD_CAPS.items():
        value = _clean_capped_text(source.get(key), cap)
        if value is not None:
            patched[key] = value

    for key, cap in _IDENTITY_FIELD_CAPS.items():
        value = _clean_capped_text(fresh_ctx.get(key), cap)
        if value is not None:
            patched[key] = value

    locale_value = _clean_capped_text(browser_locale, _BROWSER_LOCALE_CAP)
    if locale_value is not None:
        patched["browser_locale"] = locale_value

    return patched


def widget_session_error_detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
