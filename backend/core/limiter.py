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

# When set (by tests only), owner-JWT rate limiting uses this identity instead of
# decoding the bearer token. Allows tests to assert 429 without minting real JWTs.
_owner_jwt_rate_limit_key_override: Callable[[Request], str] | None = None


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
    bot_id = (
        request.query_params.get("bot_id")
        or request.headers.get("x-widget-bot-id")
        or "unknown"
    )
    ip = _widget_rate_limit_ip(request)
    return f"{bot_id[:32]}|{ip}"


def widget_bot_rate_limit_key(request: Request) -> str:
    """Global widget rate-limit identity per bot_id."""
    bot_id = (
        request.query_params.get("bot_id")
        or request.headers.get("x-widget-bot-id")
        or "unknown"
    )
    return bot_id[:32]


def widget_init_rate_limit_key(request: Request) -> str:
    """Rate-limit `/widget/session/init` by IP only."""
    return _widget_rate_limit_ip(request)


def owner_jwt_rate_limit_key(request: Request) -> str:
    """
    Rate-limit identity for owner-protected tenant endpoints (api-key rotate/revoke).

    Keys on the JWT subject (user.id) so a stolen token is throttled regardless of
    source IP. User → tenant is 1:1, so this is equivalent to per-tenant limiting.
    Falls back to remote address when no JWT is present so unauthenticated floods
    still get throttled (the route itself returns 401).
    """
    if _owner_jwt_rate_limit_key_override is not None:
        return _owner_jwt_rate_limit_key_override(request)
    if settings.environment == "test":
        return str(uuid.uuid4())

    from backend.core.security import decode_access_token

    raw_token: str | None = None
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        raw_token = auth.split(" ", 1)[1].strip()
    if not raw_token:
        raw_token = request.cookies.get("chat9_token")

    if raw_token:
        user_id = decode_access_token(raw_token)
        if user_id:
            return f"owner:{user_id}"
    return f"ip:{get_remote_address(request)}"


def hash_ip_for_logs(ip: str | None) -> str:
    value = (ip or "unknown").encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:8]


def set_widget_public_rate_limit_key_override(
    fn: Callable[[Request], str] | None,
) -> None:
    """Tests: set to e.g. ``lambda r: 'fixed'`` to assert 429; pass ``None`` to restore."""
    global _widget_public_rate_limit_key_override
    _widget_public_rate_limit_key_override = fn


def set_owner_jwt_rate_limit_key_override(
    fn: Callable[[Request], str] | None,
) -> None:
    """Tests: pin owner-JWT rate-limit identity (e.g. by header). Pass ``None`` to restore."""
    global _owner_jwt_rate_limit_key_override
    _owner_jwt_rate_limit_key_override = fn


def _limiter_storage_uri() -> str:
    """Resolve slowapi storage URI.

    Production with Redis configured → shared Redis storage (correct under
    multiple workers). Tests and local dev without Redis → in-memory.

    The `async+` prefix forces the `limits` library to use its asyncio
    Redis backend; without it slowapi calls the sync client and blocks
    the FastAPI event loop on every limited request.
    """
    if settings.environment == "test":
        return "memory://"
    if settings.redis_url:
        url = settings.redis_url
        for scheme in ("redis://", "rediss://", "unix://"):
            if url.startswith(scheme) and not url.startswith("async+"):
                return f"async+{url}"
        return url
    return "memory://"


limiter = Limiter(key_func=_key_func, storage_uri=_limiter_storage_uri())
