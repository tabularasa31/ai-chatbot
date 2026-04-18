
"""Tenant-wide disclosure level: single JSON field on Tenant (`level`)."""

from __future__ import annotations

from typing import Any

ALLOWED_LEVELS = frozenset({"detailed", "standard", "corporate"})
DEFAULT_LEVEL = "standard"


def resolve_level(raw: dict[str, Any] | None) -> str:
    """
    Effective level for prompts and API responses.
    """
    if not raw or not isinstance(raw, dict):
        return DEFAULT_LEVEL
    v = raw.get("level")
    if isinstance(v, str):
        s = v.strip()
        if s in ALLOWED_LEVELS:
            return s
    return DEFAULT_LEVEL


def public_config_dict(raw: dict[str, Any] | None) -> dict[str, str]:
    """Canonical shape returned by GET/PUT: only `level`."""
    return {"level": resolve_level(raw)}
