"""Tenant-wide support notification settings stored in Client.settings."""

from __future__ import annotations

from typing import Any


def _normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if not email:
        return None
    return email


def public_support_config_dict(settings_value: dict[str, Any] | None) -> dict[str, str | None]:
    payload = dict(settings_value or {})
    support = payload.get("support")
    if not isinstance(support, dict):
        support = {}
    return {
        "l2_email": _normalize_email(support.get("l2_email")),
    }


def with_support_config(
    settings_value: dict[str, Any] | None,
    config: dict[str, str | None],
) -> dict[str, Any]:
    payload = dict(settings_value or {})
    support = payload.get("support")
    support_payload = dict(support) if isinstance(support, dict) else {}

    l2_email = _normalize_email(config.get("l2_email"))
    if l2_email:
        support_payload["l2_email"] = l2_email
    else:
        support_payload.pop("l2_email", None)

    if support_payload:
        payload["support"] = support_payload
    else:
        payload.pop("support", None)

    return payload
