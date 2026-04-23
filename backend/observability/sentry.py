"""Optional Sentry SDK init with a no-op fallback.

No init when `SENTRY_DSN` is unset or `environment == "test"`. Keeps PII
off (no user, no default body), defers all tracing to Langfuse.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)


_initialized = False
_DEDUP_WINDOW_SECONDS = 60.0
_DEDUP_MAX_CACHE_SIZE = 1024
_recent_fingerprints: dict[tuple[str, str], float] = {}
# Sentry calls before_send from the request thread (and from background
# threads via the LoggingIntegration), so all reads/writes of the
# fingerprint cache must be serialized.
_dedup_lock = threading.Lock()


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop duplicate errors within a 60s window.

    Primary key: (error_kind tag, tenant_id) for app-tagged errors.
    Fallback key: (exception type, transaction) for OpenAI instrumentation
    errors that lack app context (e.g. APITimeoutError captured inside spans).
    """
    tags = event.get("tags") or {}
    error_kind = tags.get("error_kind") if isinstance(tags, dict) else None
    contexts = event.get("contexts") or {}
    tenant_ctx = contexts.get("tenant") if isinstance(contexts, dict) else None
    tenant_id = (
        tenant_ctx.get("tenant_id") if isinstance(tenant_ctx, dict) else None
    )

    if error_kind and tenant_id:
        key = (f"app:{error_kind}", str(tenant_id))
    else:
        exc_values = (event.get("exception") or {}).get("values") or []
        exc_type = exc_values[-1].get("type") if exc_values else None
        transaction = event.get("transaction") or ""
        if not exc_type:
            return event
        key = (f"exc:{exc_type}", transaction)
    now = time.monotonic()
    with _dedup_lock:
        last = _recent_fingerprints.get(key)
        if last is not None and (now - last) < _DEDUP_WINDOW_SECONDS:
            return None
        _recent_fingerprints[key] = now
        if len(_recent_fingerprints) > _DEDUP_MAX_CACHE_SIZE:
            cutoff = now - _DEDUP_WINDOW_SECONDS
            for k, ts in list(_recent_fingerprints.items()):
                if ts < cutoff:
                    _recent_fingerprints.pop(k, None)
            # Sustained high-cardinality storm: evict the oldest in-window
            # entries instead of clearing everything. Wholesale clear would
            # re-admit every known fingerprint to Sentry exactly when dedup
            # matters most.
            if len(_recent_fingerprints) > _DEDUP_MAX_CACHE_SIZE:
                overflow = len(_recent_fingerprints) - _DEDUP_MAX_CACHE_SIZE
                for k, _ in sorted(
                    _recent_fingerprints.items(), key=lambda item: item[1]
                )[:overflow]:
                    _recent_fingerprints.pop(k, None)
    return event


def init_sentry() -> None:
    global _initialized
    if _initialized:
        return
    if settings.environment == "test":
        return
    if not settings.sentry_dsn:
        logger.info("Sentry disabled: SENTRY_DSN not set")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError:
        logger.warning("sentry-sdk not installed; Sentry stays disabled")
        return
    try:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            release=settings.git_sha,
            traces_sample_rate=0.0,
            profiles_sample_rate=0.0,
            send_default_pii=False,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
                LoggingIntegration(event_level=logging.ERROR),
            ],
            before_send=_before_send,
        )
        sentry_sdk.set_user(None)
        _initialized = True
        logger.info("Sentry initialized", extra={"environment": settings.environment})
    except Exception:
        logger.exception("Failed to initialize Sentry; staying disabled")


def shutdown_sentry() -> None:
    global _initialized
    if not _initialized:
        return
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=2.0)
    except Exception:
        logger.exception("Sentry flush failed")
    finally:
        _initialized = False
        with _dedup_lock:
            _recent_fingerprints.clear()
