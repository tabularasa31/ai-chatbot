"""Optional Langfuse-backed tracing with a no-op fallback."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from backend.core.config import settings

logger = logging.getLogger(__name__)


class _TraceClientProtocol(Protocol):
    def trace(self, **kwargs: Any) -> Any:
        ...

    def flush(self) -> None:
        ...


class SpanHandle(ABC):
    """Interface for span-like objects."""

    @abstractmethod
    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        raise NotImplementedError


class GenerationHandle(SpanHandle, ABC):
    """Interface for generation-like objects."""

    @abstractmethod
    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        raise NotImplementedError


class TraceHandle(ABC):
    """Interface for trace-like objects."""

    @abstractmethod
    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanHandle:
        raise NotImplementedError

    @abstractmethod
    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationHandle:
        raise NotImplementedError

    @abstractmethod
    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        raise NotImplementedError


class _NoOpSpan(SpanHandle):
    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        return None


class _NoOpGeneration(GenerationHandle):
    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        return None


class _NoOpTrace(TraceHandle):
    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanHandle:
        return _NoOpSpan()

    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationHandle:
        return _NoOpGeneration()

    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        return None


@dataclass
class _LangfuseSpan(SpanHandle):
    span_obj: Any

    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = output
        if metadata:
            payload["metadata"] = metadata
        if level is not None:
            payload["level"] = level
        if status_message is not None:
            payload["status_message"] = status_message
        _safe_invoke(self.span_obj.end, **payload)


@dataclass
class _LangfuseGeneration(GenerationHandle):
    generation_obj: Any

    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = output
        if metadata:
            payload["metadata"] = metadata
        if usage:
            payload["usage"] = usage
        if level is not None:
            payload["level"] = level
        if status_message is not None:
            payload["status_message"] = status_message
        _safe_invoke(self.generation_obj.end, **payload)


@dataclass
class _LangfuseTrace(TraceHandle):
    trace_obj: Any

    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanHandle:
        span_obj = _safe_construct(
            self.trace_obj.span,
            name=name,
            input=input,
            metadata=metadata,
        )
        if span_obj is None:
            return _NoOpSpan()
        return _LangfuseSpan(span_obj)

    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationHandle:
        generation_obj = _safe_construct(
            self.trace_obj.generation,
            name=name,
            model=model,
            input=input,
            metadata=metadata,
        )
        if generation_obj is None:
            return _NoOpGeneration()
        return _LangfuseGeneration(generation_obj)

    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = output
        if metadata:
            payload["metadata"] = metadata
        if tags:
            payload["tags"] = tags
        if level is not None:
            payload["level"] = level
        if status_message is not None:
            payload["status_message"] = status_message
        _safe_invoke(self.trace_obj.update, **payload)


def _safe_construct(factory: Any, **kwargs: Any) -> Any | None:
    payload = {key: value for key, value in kwargs.items() if value is not None}
    try:
        return factory(**payload)
    except TypeError:
        logger.warning("Observability factory signature mismatch; retrying without metadata")
        fallback = {key: value for key, value in payload.items() if key != "metadata"}
        try:
            return factory(**fallback)
        except Exception:
            logger.exception("Observability factory call failed")
            return None
    except Exception:
        logger.exception("Observability factory call failed")
        return None


def _safe_invoke(func: Any, **kwargs: Any) -> None:
    payload = {key: value for key, value in kwargs.items() if value is not None}
    try:
        func(**payload)
    except TypeError:
        fallback = {
            key: value
            for key, value in payload.items()
            if key not in {"metadata", "tags", "level", "status_message", "usage"}
        }
        try:
            func(**fallback)
        except Exception:
            logger.exception("Observability end/update call failed")
    except Exception:
        logger.exception("Observability end/update call failed")


class ObservabilityService:
    """Small facade around an optional Langfuse client."""

    def __init__(self) -> None:
        self._client: _TraceClientProtocol | None = None
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
        if not (
            settings.langfuse_host
            and settings.langfuse_public_key
            and settings.langfuse_secret_key
        ):
            logger.info("Langfuse disabled: missing configuration")
            return
        try:
            from langfuse import Langfuse  # type: ignore
        except ImportError:
            logger.warning("Langfuse SDK is not installed; observability stays disabled")
            return
        try:
            self._client = Langfuse(
                host=settings.langfuse_host,
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
            )
            self._enabled = True
            logger.info("Langfuse observability initialized")
        except Exception:
            logger.exception("Failed to initialize Langfuse; observability stays disabled")
            self.reset()

    def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            logger.exception("Failed to flush Langfuse client during shutdown")
        finally:
            self.reset()

    def begin_trace(
        self,
        *,
        name: str,
        session_id: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> TraceHandle:
        if self._client is None:
            return _NoOpTrace()
        trace_obj = _safe_construct(
            self._client.trace,
            name=name,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
            tags=tags,
        )
        if trace_obj is None:
            return _NoOpTrace()
        return _LangfuseTrace(trace_obj)


_service = ObservabilityService()


def init_observability() -> None:
    _service.init()


def shutdown_observability() -> None:
    _service.shutdown()


def get_observability() -> ObservabilityService:
    return _service


def begin_trace(
    *,
    name: str,
    session_id: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> TraceHandle:
    return _service.begin_trace(
        name=name,
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
        tags=tags,
    )
