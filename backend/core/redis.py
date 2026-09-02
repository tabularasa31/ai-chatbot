"""Async Redis client with graceful-degradation helpers.

Redis is foundational infra (rate-limit storage, caches, distributed locks),
but it is **not** on the critical path for chat. When `REDIS_URL` is unset or
the server is unreachable, callers degrade gracefully:

- `get_redis()` returns `None` when Redis is disabled (no URL configured).
- `cache_get` / `cache_set_with_ttl` swallow connection errors and return
  `None` / `False` — the caller proceeds as if there were a cache miss.
- `acquire_lock` returns `None` on failure — the caller may decide whether
  to skip the work or run it unguarded.
- `redis_ping` returns `False` on any error — `/health` reports `unavailable`
  but the request still succeeds.

slowapi rate-limit storage is wired in `backend/core/limiter.py` directly via
`storage_uri`; failures there surface as 500s rather than silent fallback,
because rate limiting is a security control.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, TypeVar

from backend.core.config import settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_client: Redis | None = None
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def is_enabled() -> bool:
    """True when a Redis URL is configured. Does not check connectivity."""
    return bool(settings.redis_url)


async def init_redis() -> None:
    """Open the shared async Redis client. No-op when `REDIS_URL` is unset.

    Idempotent: a second call while a client is live is a no-op, so repeated
    lifespan starts (hot-reload, test fixtures) do not leak connection pools.

    Connection errors at startup are logged but not raised — Redis is
    optional infra, and the app must boot even if Redis is briefly down.
    """
    global _client
    if _client is not None:
        return
    if not settings.redis_url:
        logger.info("redis_disabled: REDIS_URL not configured")
        return

    from redis.asyncio import Redis

    _client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
        health_check_interval=30,
    )
    try:
        await _client.ping()
        logger.info("redis_connected")
    except Exception as exc:
        logger.warning("redis_init_ping_failed: %s", exc)


async def shutdown_redis() -> None:
    """Close the shared client and release the connection pool."""
    global _client
    if _client is None:
        return
    try:
        await _client.aclose()
    except Exception as exc:
        logger.warning("redis_shutdown_failed: %s", exc)
    finally:
        _client = None


def get_redis() -> Redis | None:
    """Return the live client, or `None` when Redis is disabled.

    Callers that need Redis must handle `None` and connection errors.
    """
    return _client


async def redis_ping() -> bool:
    """True when the live client responds to PING. False otherwise."""
    if _client is None:
        return False
    try:
        return bool(await _client.ping())
    except Exception:
        return False


async def cache_get(key: str) -> str | None:
    """Get a cached string value. Returns `None` on miss or any error."""
    if _client is None:
        return None
    try:
        return await _client.get(key)
    except Exception as exc:
        logger.debug("redis_cache_get_failed key=%s: %s", key, exc)
        return None


async def cache_set_with_ttl(key: str, value: str, ttl_seconds: int) -> bool:
    """Set a cached string value with TTL. Returns False on any error."""
    if _client is None or ttl_seconds <= 0:
        return False
    try:
        await _client.set(key, value, ex=ttl_seconds)
        return True
    except Exception as exc:
        logger.debug("redis_cache_set_failed key=%s: %s", key, exc)
        return False


async def acquire_lock(key: str, ttl_seconds: int) -> str | None:
    """Acquire a TTL-bounded lock. Returns the token to pass to `release_lock`,
    or `None` if the lock is held by someone else / Redis is unavailable.
    """
    if _client is None or ttl_seconds <= 0:
        return None
    token = secrets.token_hex(16)
    try:
        acquired = await _client.set(key, token, nx=True, ex=ttl_seconds)
    except Exception as exc:
        logger.debug("redis_lock_acquire_failed key=%s: %s", key, exc)
        return None
    return token if acquired else None


async def release_lock(key: str, token: str) -> bool:
    """Release a lock only if the token matches (no-op otherwise).

    The check-and-delete is atomic via a Lua script so we never delete
    a lock another holder acquired after our TTL expired.
    """
    if _client is None or not token:
        return False
    try:
        result = await _client.eval(_RELEASE_LOCK_LUA, 1, key, token)
        return bool(result)
    except Exception as exc:
        logger.debug("redis_lock_release_failed key=%s: %s", key, exc)
        return False


_T = TypeVar("_T")


def _run_coro_sync(
    make_coro: Callable[[], Coroutine[object, object, _T]],
    *,
    timeout: float,
    default: _T,
    label: str,
) -> _T:
    """Run a Redis coroutine to completion from a non-loop (daemon) thread.

    The shared async client is bound to the app's main event loop, so callers
    running outside it (e.g. ``PeriodicJob`` daemon threads) must marshal the
    coroutine back onto that loop with ``run_coroutine_threadsafe`` — the same
    bridge ``crawl_url`` uses for its sync enqueue path.

    ``make_coro`` is a factory (not a coroutine) so nothing is scheduled when
    the loop is unavailable, avoiding an un-awaited-coroutine warning. On
    timeout the pending future is cancelled best-effort: if the underlying
    ``SET``/``GET`` already ran on the loop the effect is harmless (a lock
    self-heals at its TTL; a marker set is idempotent), but cancelling stops us
    from leaking a holder whose token we've already discarded.
    """
    from backend.core.queue import get_main_loop

    loop = get_main_loop()
    if loop is None or not loop.is_running():
        return default
    future = None
    try:
        future = asyncio.run_coroutine_threadsafe(make_coro(), loop)
        return future.result(timeout=timeout)
    except Exception as exc:
        if future is not None:
            future.cancel()
        logger.debug("%s failed: %s", label, exc)
        return default


def acquire_lock_sync(key: str, ttl_seconds: int, *, timeout: float = 3.0) -> str | None:
    """Blocking :func:`acquire_lock` for daemon threads. See :func:`_run_coro_sync`.

    Returns the lock token, or ``None`` when the lock is held elsewhere, the
    main loop is unavailable, or Redis is unreachable.
    """
    return _run_coro_sync(
        lambda: acquire_lock(key, ttl_seconds),
        timeout=timeout,
        default=None,
        label=f"redis_lock_acquire_sync key={key}",
    )


def release_lock_sync(key: str, token: str, *, timeout: float = 3.0) -> bool:
    """Blocking :func:`release_lock` for daemon threads. Best-effort: an
    unreleased lock simply expires at its TTL."""
    return bool(
        _run_coro_sync(
            lambda: release_lock(key, token),
            timeout=timeout,
            default=False,
            label=f"redis_lock_release_sync key={key}",
        )
    )


def cache_get_sync(key: str, *, timeout: float = 3.0) -> str | None:
    """Blocking :func:`cache_get` for daemon threads. Returns ``None`` on miss
    or any error (caller treats it as 'not present')."""
    return _run_coro_sync(
        lambda: cache_get(key),
        timeout=timeout,
        default=None,
        label=f"redis_cache_get_sync key={key}",
    )


def cache_set_sync(key: str, value: str, ttl_seconds: int, *, timeout: float = 3.0) -> bool:
    """Blocking :func:`cache_set_with_ttl` for daemon threads."""
    return bool(
        _run_coro_sync(
            lambda: cache_set_with_ttl(key, value, ttl_seconds),
            timeout=timeout,
            default=False,
            label=f"redis_cache_set_sync key={key}",
        )
    )
