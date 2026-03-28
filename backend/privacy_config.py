"""Tenant privacy / redaction configuration helpers."""

from __future__ import annotations

from typing import Any

from backend.chat.pii import DEFAULT_OPTIONAL_ENTITY_TYPES, OPTIONAL_ENTITY_TYPES


DEFAULT_REDACTION_CONFIG = {
    "optional_entity_types": sorted(DEFAULT_OPTIONAL_ENTITY_TYPES),
}


def public_redaction_config_dict(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    optional = DEFAULT_REDACTION_CONFIG["optional_entity_types"]
    if isinstance(raw, dict):
        nested = raw.get("redaction")
        if isinstance(nested, dict):
            stored = nested.get("optional_entity_types")
            if isinstance(stored, list):
                filtered = [item for item in stored if item in OPTIONAL_ENTITY_TYPES]
                optional = sorted(set(filtered))
    return {"optional_entity_types": list(optional)}


def with_redaction_config(settings_value: dict[str, Any] | None, config: dict[str, list[str]]) -> dict[str, Any]:
    payload = dict(settings_value or {})
    payload["redaction"] = {
        "optional_entity_types": [
            item for item in config["optional_entity_types"] if item in OPTIONAL_ENTITY_TYPES
        ]
    }
    return payload
