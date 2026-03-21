"""Rate limiter for FastAPI using slowapi."""

from __future__ import annotations

import uuid
from typing import Callable, Optional

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from backend.core.config import settings

# When set (by tests only), `/widget/chat` rate limiting uses this identity instead of IP.
_widget_public_rate_limit_key_override: Optional[Callable[[Request], str]] = None


def _key_func(request):
    """Use unique key in test mode to avoid rate limiting tests."""
    if settings.environment == "test":
        return str(uuid.uuid4())
    return get_remote_address(request)


def widget_public_rate_limit_key(request: Request) -> str:
    """
    Rate-limit identity for the public widget chat endpoint.

    slowapi captures this callable at import time; tests pin behavior via
    `_widget_public_rate_limit_key_override` so the same key is used across requests.
    """
    if _widget_public_rate_limit_key_override is not None:
        return _widget_public_rate_limit_key_override(request)
    return get_remote_address(request)


def set_widget_public_rate_limit_key_override(
    fn: Optional[Callable[[Request], str]],
) -> None:
    """Tests: set to e.g. ``lambda r: 'fixed'`` to assert 429; pass ``None`` to restore."""
    global _widget_public_rate_limit_key_override
    _widget_public_rate_limit_key_override = fn


limiter = Limiter(key_func=_key_func)
