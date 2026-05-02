"""Idempotency-Key support for non-streaming write endpoints.

Clients (widget, mobile, server-to-server) may retry a POST after a network
timeout while the server is still processing. Without deduplication this
re-runs the handler, double-charges the tenant's LLM key, and shows duplicate
messages in chat history. The `Idempotency-Key` header lets the client mark
retries of the same logical operation; the server replays the first stored
response for the lifetime of the key.

Storage is Redis (via `backend/core/redis.py`). When Redis is unavailable
the helper degrades to a no-op — handlers run normally without dedup.

Flow per request:
1. Read `Idempotency-Key` header. If absent, run handler unguarded
   (backwards-compatible).
2. Lookup `idempotency:<scope>:<tenant_id>:<key>:response`. If hit, replay
   the cached response (status + body) and skip the handler.
3. Acquire `idempotency:<scope>:<tenant_id>:<key>:lock` with short TTL. On
   success, run handler; on completion store the response and release lock.
4. If lock acquisition fails, a sibling request is in flight. Poll the cache
   briefly; if it appears, replay. Otherwise raise 409 Conflict.

Scope (`chat`, `escalate`, etc.) keeps keys partitioned per logical operation,
so a client reusing one key across two endpoints does not get a wrong replay.

Out of scope (per ticket 86exf7z76): SSE streams (`Content-Type: text/event-stream`)
have different semantics and are handled separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from backend.core import redis as redis_module

logger = logging.getLogger(__name__)

DEFAULT_RESPONSE_TTL_SECONDS = 24 * 60 * 60
# Lock TTL must outlast a realistic handler run. The chat pipeline can take
# 60s+ on slow OpenAI calls; if the lock expires before the handler stores
# a response, a retry would acquire a fresh lock and re-execute the work,
# defeating the dedup guarantee. 120s comfortably covers chat-class handlers.
DEFAULT_LOCK_TTL_SECONDS = 120
PARALLEL_POLL_TOTAL_SECONDS = 5.0
PARALLEL_POLL_INTERVAL_SECONDS = 0.2
MAX_KEY_LENGTH = 128


@dataclass(frozen=True)
class CachedResponse:
    """A response previously stored for an idempotency key."""

    status_code: int
    body: Any


def _read_header(request: Request) -> str | None:
    """Return the Idempotency-Key header value, validated and trimmed.

    Returns None for missing/empty/oversized values — callers treat that as
    "no idempotency requested" and proceed without dedup.
    """
    raw = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if not raw:
        return None
    key = raw.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        return None
    return key


def _response_cache_key(scope: str, tenant_id: str, key: str) -> str:
    return f"idempotency:{scope}:{tenant_id}:{key}:response"


def _lock_cache_key(scope: str, tenant_id: str, key: str) -> str:
    return f"idempotency:{scope}:{tenant_id}:{key}:lock"


async def _load_cached(cache_key: str) -> CachedResponse | None:
    raw = await redis_module.cache_get(cache_key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return CachedResponse(
            status_code=int(payload["status_code"]),
            body=payload["body"],
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("idempotency_cache_decode_failed key=%s: %s", cache_key, exc)
        return None


async def _wait_for_sibling_response(
    cache_key: str,
    *,
    total_seconds: float = PARALLEL_POLL_TOTAL_SECONDS,
    interval_seconds: float = PARALLEL_POLL_INTERVAL_SECONDS,
) -> CachedResponse | None:
    """Poll the response cache while a sibling request holds the lock.

    The lock is held only for the duration of the handler; once the sibling
    finishes and stores its response, the cache key appears. We bail out
    after `total_seconds` and let the caller raise 409.
    """
    deadline = asyncio.get_event_loop().time() + total_seconds
    while True:
        cached = await _load_cached(cache_key)
        if cached is not None:
            return cached
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(interval_seconds)


class IdempotencySection:
    """Per-request handle yielded by `idempotent_section`.

    The handler checks `cached` first and replays if present; otherwise it
    produces a response and calls `record` before returning.
    """

    def __init__(
        self,
        *,
        cached: CachedResponse | None,
        response_cache_key: str | None,
        lock_cache_key: str | None,
        lock_token: str | None,
        response_ttl_seconds: int,
    ) -> None:
        self.cached = cached
        self._response_cache_key = response_cache_key
        self._lock_cache_key = lock_cache_key
        self._lock_token = lock_token
        self._response_ttl_seconds = response_ttl_seconds
        self._recorded = False

    @property
    def active(self) -> bool:
        """True when an Idempotency-Key was supplied and Redis is available."""
        return self._response_cache_key is not None

    async def record(self, *, status_code: int, body: Any) -> None:
        """Persist the response so retries replay it instead of re-running."""
        if not self.active or self._recorded:
            return
        if self._response_cache_key is None:
            return
        try:
            payload = json.dumps({"status_code": status_code, "body": body})
        except (TypeError, ValueError) as exc:
            logger.warning("idempotency_response_encode_failed: %s", exc)
            return
        await redis_module.cache_set_with_ttl(
            self._response_cache_key, payload, self._response_ttl_seconds
        )
        self._recorded = True

    async def _release_lock(self) -> None:
        if self._lock_cache_key and self._lock_token:
            await redis_module.release_lock(self._lock_cache_key, self._lock_token)
            self._lock_token = None


@asynccontextmanager
async def idempotent_section(
    request: Request,
    *,
    tenant_id: str,
    scope: str,
    response_ttl_seconds: int = DEFAULT_RESPONSE_TTL_SECONDS,
    lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> AsyncIterator[IdempotencySection]:
    """Context manager for idempotent JSON endpoints.

    Usage:

        async with idempotent_section(request, tenant_id=str(tenant.id), scope="chat") as section:
            if section.cached:
                return JSONResponse(status_code=section.cached.status_code, content=section.cached.body)
            payload = MyResponse(...).model_dump(mode="json")
            await section.record(status_code=200, body=payload)
            return JSONResponse(status_code=200, content=payload)

    No-ops (yields a section with `active=False` and `cached=None`) when:
    - the client did not send `Idempotency-Key`, or
    - Redis is not configured / unreachable.

    Raises HTTPException(409) if a sibling request holds the lock and does
    not finish within the polling window.
    """
    key = _read_header(request)
    if not key or not redis_module.is_enabled():
        yield IdempotencySection(
            cached=None,
            response_cache_key=None,
            lock_cache_key=None,
            lock_token=None,
            response_ttl_seconds=response_ttl_seconds,
        )
        return

    response_key = _response_cache_key(scope, tenant_id, key)
    lock_key = _lock_cache_key(scope, tenant_id, key)

    cached = await _load_cached(response_key)
    if cached is not None:
        yield IdempotencySection(
            cached=cached,
            response_cache_key=response_key,
            lock_cache_key=lock_key,
            lock_token=None,
            response_ttl_seconds=response_ttl_seconds,
        )
        return

    lock_token = await redis_module.acquire_lock(lock_key, lock_ttl_seconds)
    if lock_token is None:
        # `acquire_lock` returns None for two distinct reasons: (a) a sibling
        # holds the lock (real in-flight duplicate), or (b) Redis is
        # unreachable (transient outage). We must not fail keyed requests on
        # (b) — graceful degradation means running unguarded when the cache
        # layer is gone. Use redis_ping to disambiguate: alive → treat as
        # sibling and poll/409; dead → run handler unguarded.
        if not await redis_module.redis_ping():
            logger.warning("idempotency_redis_unavailable_degrading_to_noop")
            yield IdempotencySection(
                cached=None,
                response_cache_key=None,
                lock_cache_key=None,
                lock_token=None,
                response_ttl_seconds=response_ttl_seconds,
            )
            return
        sibling = await _wait_for_sibling_response(response_key)
        if sibling is not None:
            yield IdempotencySection(
                cached=sibling,
                response_cache_key=response_key,
                lock_cache_key=lock_key,
                lock_token=None,
                response_ttl_seconds=response_ttl_seconds,
            )
            return
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_in_flight",
                "message": "A request with this Idempotency-Key is still being processed.",
            },
        )

    section = IdempotencySection(
        cached=None,
        response_cache_key=response_key,
        lock_cache_key=lock_key,
        lock_token=lock_token,
        response_ttl_seconds=response_ttl_seconds,
    )
    try:
        yield section
    finally:
        await section._release_lock()
