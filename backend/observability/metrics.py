"""Optional PostHog-backed product metrics with a no-op fallback.

Mirrors the shape of `backend.observability.service` (Langfuse facade):
zero-cost when disabled, no real HTTP in tests, single module-level
singleton accessed via `init_metrics`/`shutdown_metrics`/`capture_event`.

Event emission is intentionally NOT wired here yet — this module only
provides the facade and lifecycle. Call sites land in follow-up PRs.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from backend.core.config import settings

logger = logging.getLogger(__name__)


class _PostHogClientProtocol(Protocol):
    def capture(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def flush(self) -> None:
        ...

    def shutdown(self) -> None:
        ...


_RESERVED_PROPERTY_KEYS = {"environment", "release", "tenant_id", "bot_id"}


class MetricsService:
    """Facade around an optional PostHog client.

    All methods are safe to call regardless of init state — when the
    client is unconfigured (missing key, env=test, SDK error) every
    operation is a no-op.
    """

    def __init__(self) -> None:
        self._client: _PostHogClientProtocol | None = None
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def reset(self) -> None:
        self._client = None
        self._enabled = False

    def init(self) -> None:
        if self._client is not None or self._enabled:
            return
        if settings.environment == "test":
            logger.info("PostHog disabled: environment=test")
            return
        if not settings.posthog_api_key:
            logger.info("PostHog disabled: POSTHOG_API_KEY not set")
            return
        try:
            from posthog import Posthog  # type: ignore
        except ImportError:
            logger.warning("posthog SDK is not installed; metrics stay disabled")
            return
        try:
            self._client = Posthog(
                project_api_key=settings.posthog_api_key,
                host=settings.posthog_host,
            )
            self._enabled = True
            logger.info(
                "PostHog metrics initialized",
                extra={"posthog_host": settings.posthog_host},
            )
        except Exception:
            logger.exception("Failed to initialize PostHog; metrics stay disabled")
            self.reset()

    def shutdown(self) -> None:
        if self._client is None:
            self.reset()
            return
        try:
            shutdown = getattr(self._client, "shutdown", None)
            if callable(shutdown):
                shutdown()
            else:
                self._client.flush()
        except Exception:
            logger.exception("Failed to flush PostHog client during shutdown")
        finally:
            self.reset()

    def flush(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            logger.exception("PostHog flush failed")

    def capture(
        self,
        event: str,
        *,
        distinct_id: str,
        tenant_id: str | None = None,
        bot_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled or self._client is None:
            return
        merged: dict[str, Any] = {}
        if properties:
            merged.update(
                {k: v for k, v in properties.items() if k not in _RESERVED_PROPERTY_KEYS}
            )
        merged["environment"] = settings.environment
        if settings.git_sha:
            merged["release"] = settings.git_sha
        if tenant_id is not None:
            merged["tenant_id"] = tenant_id
        if bot_id is not None:
            merged["bot_id"] = bot_id
        try:
            self._client.capture(
                distinct_id=distinct_id,
                event=event,
                properties=merged,
            )
        except Exception:
            logger.exception("PostHog capture failed", extra={"event": event})


_service = MetricsService()


def init_metrics() -> None:
    _service.init()


def shutdown_metrics() -> None:
    _service.shutdown()


def get_metrics() -> MetricsService:
    return _service


def capture_event(
    event: str,
    *,
    distinct_id: str,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    _service.capture(
        event,
        distinct_id=distinct_id,
        tenant_id=tenant_id,
        bot_id=bot_id,
        properties=properties,
    )
