"""Tenant-wide support notification settings stored in Client.settings."""

from __future__ import annotations

import re
from typing import Any

# Minimal BCP-47 validator: primary subtag (2-3 alpha) optionally followed by
# script/region/variant subtags separated by hyphens.  This rejects freeform
# words like "english" or "not-a-tag" before they reach LLM prompts.
_BCP47_RE = re.compile(r"^[a-z]{2,3}(-[a-zA-Z0-9]{2,8})*$", re.IGNORECASE)


def _normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if not email:
        return None
    return email


def _normalize_language(value: Any) -> str | None:
    """Return *value* if it looks like a valid BCP-47 language tag, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not _BCP47_RE.match(text):
        return None
    return text


def public_support_config_dict(settings_value: dict[str, Any] | None) -> dict[str, str | None]:
    payload = dict(settings_value or {})
    support = payload.get("support")
    if not isinstance(support, dict):
        support = {}
    return {
        "l2_email": _normalize_email(support.get("l2_email")),
        "escalation_language": _normalize_language(support.get("escalation_language")),
    }


def with_support_config(
    settings_value: dict[str, Any] | None,
    config: dict[str, str | None],
) -> dict[str, Any]:
    """Merge *config* into *settings_value*.

    Only keys **present** in *config* are written or cleared; absent keys are
    left unchanged.  Passing ``{"l2_email": "a@b.com"}`` therefore never
    touches an existing ``escalation_language`` value, which prevents partial
    PUT requests from silently deleting settings the client did not intend to
    change.
    """
    payload = dict(settings_value or {})
    support = payload.get("support")
    support_payload = dict(support) if isinstance(support, dict) else {}

    if "l2_email" in config:
        l2_email = _normalize_email(config["l2_email"])
        if l2_email:
            support_payload["l2_email"] = l2_email
        else:
            support_payload.pop("l2_email", None)

    if "escalation_language" in config:
        escalation_language = _normalize_language(config["escalation_language"])
        if escalation_language:
            support_payload["escalation_language"] = escalation_language
        else:
            support_payload.pop("escalation_language", None)

    if support_payload:
        payload["support"] = support_payload
    else:
        payload.pop("support", None)

    return payload
