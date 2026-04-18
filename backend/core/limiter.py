"""Rate limiter for FastAPI using slowapi."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from backend.core.config import settings

# When set (by tests only), `/widget/chat` rate limiting uses this identity instead of IP.
_widget_public_rate_limit_key_override: Callable[[Request], str] | None = None


def _widget_rate_limit_ip(request: Request) -> str:
    if _widget_public_rate_limit_key_override is not None:
        return _widget_public_rate_limit_key_override(request)
    return get_remote_address(request)


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
    client_id = (
        request.query_params.get("client_id")
        or request.headers.get("x-widget-client-id")
        or "unknown"
    )
    ip = _widget_rate_limit_ip(request)
    return f"{client_id[:32]}|{ip}"


def widget_client_rate_limit_key(request: Request) -> str:
    """Global widget rate-limit identity per client_id."""
    client_id = (
        request.query_params.get("client_id")
        or request.headers.get("x-widget-client-id")
        or "unknown"
    )
    return client_id[:32]


def widget_init_rate_limit_key(request: Request) -> str:
    """Rate-limit `/widget/session/init` by IP only."""
    return _widget_rate_limit_ip(request)


def hash_ip_for_logs(ip: str | None) -> str:
    value = (ip or "unknown").encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:8]


def set_widget_public_rate_limit_key_override(
    fn: Callable[[Request], str] | None,
) -> None:
    """Tests: set to e.g. ``lambda r: 'fixed'`` to assert 429; pass ``None`` to restore."""
    global _widget_public_rate_limit_key_override
    _widget_public_rate_limit_key_override = fn


limiter = Limiter(key_func=_key_func)
