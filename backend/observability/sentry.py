"""Optional Sentry SDK init with a no-op fallback.

No init when `SENTRY_DSN` is unset or `environment == "test"`. Keeps PII
off (no user, no default body), defers all tracing to Langfuse.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)


_initialized = False
_dedup_window_seconds = 60.0
_recent_fingerprints: dict[tuple[str, str], float] = {}


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop duplicate (error_kind, tenant_id) within a 60s window."""
    tags = event.get("tags") or {}
    error_kind = tags.get("error_kind") if isinstance(tags, dict) else None
    contexts = event.get("contexts") or {}
    tenant_ctx = contexts.get("tenant") if isinstance(contexts, dict) else None
    tenant_id = (
        tenant_ctx.get("tenant_id") if isinstance(tenant_ctx, dict) else None
    )
    if not error_kind or not tenant_id:
        return event
    key = (str(error_kind), str(tenant_id))
    now = time.monotonic()
    last = _recent_fingerprints.get(key)
    if last is not None and (now - last) < _dedup_window_seconds:
        return None
    _recent_fingerprints[key] = now
    if len(_recent_fingerprints) > 1024:
        cutoff = now - _dedup_window_seconds
        for k, ts in list(_recent_fingerprints.items()):
            if ts < cutoff:
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
        logger.warning("Sentry initialized", extra={"environment": settings.environment})
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
        _recent_fingerprints.clear()
