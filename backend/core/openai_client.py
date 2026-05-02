"""OpenAI client factory — uses per-client API key (encrypted in DB).

Clients are cached per (key fingerprint, timeout, sync|async) so the underlying
``httpx`` connection pool / TLS keepalive amortizes across the ~7-10 OpenAI
calls a single chat turn fans out (injection guard, relevance guard, embed,
semantic rewrite, generate, validate, etc.). Without this each call paid a
fresh TLS handshake to ``api.openai.com`` (~200-500 ms WAN) and burned the
keepalive of the prior client.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections import OrderedDict

import httpx
from fastapi import HTTPException
from openai import AsyncOpenAI, OpenAI, RateLimitError

from backend.core.config import settings
from backend.core.crypto import decrypt_value

logger = logging.getLogger(__name__)

# Fast timeouts for non-data phases — failures here surface quickly.
_CONNECT_TIMEOUT_SECONDS = 10.0
_WRITE_TIMEOUT_SECONDS = 10.0
_POOL_TIMEOUT_SECONDS = 10.0

# Bounded LRU for client reuse. One entry per (tenant key, timeout, sync/async)
# tuple. With a handful of distinct timeouts and a small fleet of tenants, 128
# entries is plenty; oldest clients are evicted (and their httpx pools GCed).
_CLIENT_CACHE_MAX = 128
_client_cache: OrderedDict[tuple[str, float, str], OpenAI | AsyncOpenAI] = OrderedDict()
_client_cache_lock = threading.Lock()


def _fingerprint_key(decrypted_key: str) -> str:
    """Hash the decrypted key so the cache map never holds the raw secret."""
    return hashlib.sha256(decrypted_key.encode("utf-8")).hexdigest()


def _close_client_best_effort(client: OpenAI | AsyncOpenAI) -> None:
    """Release an evicted client's httpx pool / sockets.

    The OpenAI SDK exposes a sync ``close()`` for the sync client and an
    ``async close()`` coroutine for the async one. For ``AsyncOpenAI`` we
    schedule the close on the running loop when one is available; otherwise
    we fall back to closing the underlying httpx async transport directly via
    its sync ``close`` (httpx supports it for both sync and async clients).
    All errors are swallowed — eviction must not break the calling request.
    """
    try:
        if isinstance(client, AsyncOpenAI):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(client.close())
                return
            inner = getattr(client, "_client", None)
            closer = getattr(inner, "close", None) if inner is not None else None
            if callable(closer):
                closer()
        else:
            client.close()
    except Exception:
        logger.debug("openai_client_close_failed", exc_info=True)


def _cache_get(key: tuple[str, float, str]) -> OpenAI | AsyncOpenAI | None:
    with _client_cache_lock:
        client = _client_cache.get(key)
        if client is not None:
            _client_cache.move_to_end(key)
        return client


def _cache_put(key: tuple[str, float, str], client: OpenAI | AsyncOpenAI) -> None:
    evicted: list[OpenAI | AsyncOpenAI] = []
    with _client_cache_lock:
        _client_cache[key] = client
        _client_cache.move_to_end(key)
        while len(_client_cache) > _CLIENT_CACHE_MAX:
            _, evicted_client = _client_cache.popitem(last=False)
            evicted.append(evicted_client)
    # Close outside the lock — close() can do I/O / scheduling and we don't
    # want it to serialise the next cache insertion.
    for old in evicted:
        _close_client_best_effort(old)


def _reset_cache() -> None:
    """Test hook: drop all cached clients and close their httpx pools."""
    with _client_cache_lock:
        evicted = list(_client_cache.values())
        _client_cache.clear()
    for old in evicted:
        _close_client_best_effort(old)


def _decrypt_or_raise(encrypted_key: str | None) -> str:
    if not encrypted_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard.",
        )
    try:
        return decrypt_value(encrypted_key)
    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to decrypt OpenAI API key.",
        ) from e


def _build_timeout(read_timeout: float) -> httpx.Timeout:
    # Separate connect vs read: connect failure surfaces fast; slow LLM responses
    # (including the wait for the first streaming chunk) get the full read budget.
    # Note: read_timeout >> openai_user_retry_budget_seconds by design — a timeout
    # error already exhausts the retry budget, so no retry is attempted.
    return httpx.Timeout(
        connect=_CONNECT_TIMEOUT_SECONDS,
        read=read_timeout,
        write=_WRITE_TIMEOUT_SECONDS,
        pool=_POOL_TIMEOUT_SECONDS,
    )


def get_openai_client(encrypted_key: str | None, *, timeout: float | None = None) -> OpenAI:
    """
    Return a cached OpenAI client for the decrypted API key + timeout pair.

    Args:
        encrypted_key: Encrypted value from client.openai_api_key (DB).
        timeout: Optional total read timeout override (seconds). When omitted, uses
            ``OPENAI_REQUEST_TIMEOUT_SECONDS`` as the read timeout.

    Raises:
        HTTPException 400: Key not configured.
        HTTPException 500: Decryption failed.
    """
    decrypted_key = _decrypt_or_raise(encrypted_key)
    read_timeout = timeout if timeout is not None else settings.openai_request_timeout_seconds
    cache_key = (_fingerprint_key(decrypted_key), float(read_timeout), "sync")
    cached = _cache_get(cache_key)
    if isinstance(cached, OpenAI):
        return cached
    client = OpenAI(
        api_key=decrypted_key,
        timeout=_build_timeout(read_timeout),
        max_retries=0,
    )
    _cache_put(cache_key, client)
    return client


def get_async_openai_client(
    encrypted_key: str | None, *, timeout: float | None = None
) -> AsyncOpenAI:
    """Async counterpart of :func:`get_openai_client`.

    Same key-decryption, timeout policy and cache semantics as the sync factory;
    returns an ``AsyncOpenAI`` instance for use from async services. Sync callers
    stay on ``get_openai_client``.
    """
    decrypted_key = _decrypt_or_raise(encrypted_key)
    read_timeout = timeout if timeout is not None else settings.openai_request_timeout_seconds
    cache_key = (_fingerprint_key(decrypted_key), float(read_timeout), "async")
    cached = _cache_get(cache_key)
    if isinstance(cached, AsyncOpenAI):
        return cached
    client = AsyncOpenAI(
        api_key=decrypted_key,
        timeout=_build_timeout(read_timeout),
        max_retries=0,
    )
    _cache_put(cache_key, client)
    return client


# Reasoning / o-series models that reject custom temperature and other params.
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def is_reasoning_model(model: str) -> bool:
    """Return True for OpenAI reasoning models that restrict sampling parameters."""
    m = model.lower()
    return any(m == p or m.startswith(p + "-") for p in _REASONING_MODEL_PREFIXES)


def is_quota_exceeded(exc: RateLimitError) -> bool:
    """Return True when the OpenAI error is an insufficient_quota / billing error."""
    body = getattr(exc, "body", None) or {}
    if isinstance(body, dict):
        error = body.get("error") or {}
        return error.get("code") == "insufficient_quota"
    return "insufficient_quota" in str(body)
