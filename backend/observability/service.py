"""Optional Langfuse-backed tracing with a no-op fallback."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from dataclasses import dataclass
import random
import time
from collections import defaultdict, deque
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

    def promote(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        return None

    @property
    def sampled(self) -> bool:
        return True


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

    @property
    def sampled(self) -> bool:
        return False


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

    @property
    def sampled(self) -> bool:
        return True


class _DeferredSpan(SpanHandle):
    def __init__(self, trace: "_DeferredTrace", *, kind: str, kwargs: dict[str, Any]) -> None:
        self._trace = trace
        self._kind = kind
        self._kwargs = kwargs

    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        self._trace._operations.append(
            {
                "kind": self._kind,
                "kwargs": dict(self._kwargs),
                "end": {
                    "output": output,
                    "metadata": metadata,
                    "level": level,
                    "status_message": status_message,
                },
            }
        )


class _DeferredGeneration(GenerationHandle):
    def __init__(self, trace: "_DeferredTrace", *, kwargs: dict[str, Any]) -> None:
        self._trace = trace
        self._kwargs = kwargs

    def end(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        self._trace._operations.append(
            {
                "kind": "generation",
                "kwargs": dict(self._kwargs),
                "end": {
                    "output": output,
                    "metadata": metadata,
                    "usage": usage,
                    "level": level,
                    "status_message": status_message,
                },
            }
        )


class _DeferredTrace(TraceHandle):
    def __init__(
        self,
        service: "ObservabilityService",
        *,
        init_kwargs: dict[str, Any],
        sampled: bool,
        sampling_reason: str,
    ) -> None:
        self._service = service
        self._init_kwargs = init_kwargs
        self._sampled = sampled
        self._sampling_reason = sampling_reason
        self._operations: list[dict[str, Any]] = []
        self._materialized: TraceHandle | None = None

    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanHandle:
        if self._materialized is not None:
            return self._materialized.span(name=name, input=input, metadata=metadata)
        return _DeferredSpan(
            self,
            kind="span",
            kwargs={"name": name, "input": input, "metadata": metadata},
        )

    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationHandle:
        if self._materialized is not None:
            return self._materialized.generation(
                name=name,
                model=model,
                input=input,
                metadata=metadata,
            )
        return _DeferredGeneration(
            self,
            kwargs={"name": name, "model": model, "input": input, "metadata": metadata},
        )

    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if self._materialized is not None:
            self._materialized.update(
                output=output,
                metadata=metadata,
                tags=tags,
                level=level,
                status_message=status_message,
            )
            return
        self._operations.append(
            {
                "kind": "update",
                "kwargs": {
                    "output": output,
                    "metadata": metadata,
                    "tags": tags,
                    "level": level,
                    "status_message": status_message,
                },
            }
        )

    def promote(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        if self._materialized is not None:
            if metadata or tags:
                self._materialized.update(metadata=metadata, tags=tags)
            return
        materialized = self._service._materialize_trace(
            init_kwargs=self._init_kwargs,
            metadata=metadata,
            tags=tags,
            sampling_reason=self._sampling_reason,
        )
        if materialized is None:
            return
        self._materialized = materialized
        for operation in self._operations:
            kind = operation["kind"]
            if kind == "update":
                self._materialized.update(**operation["kwargs"])
                continue
            if kind == "span":
                span = self._materialized.span(**operation["kwargs"])
                span.end(**operation["end"])
                continue
            if kind == "generation":
                generation = self._materialized.generation(**operation["kwargs"])
                generation.end(**operation["end"])
        self._operations.clear()

    @property
    def sampled(self) -> bool:
        return self._sampled or self._materialized is not None


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
        self._tenant_query_counts: dict[str, int] = defaultdict(int)
        self._tenant_recent_queries: dict[str, deque[float]] = defaultdict(deque)

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

    def _record_tenant_query(self, tenant_id: str) -> tuple[int, int]:
        now = time.time()
        recent = self._tenant_recent_queries[tenant_id]
        recent.append(now)
        window_seconds = max(int(settings.trace_rate_window_seconds), 1)
        cutoff = now - window_seconds
        while recent and recent[0] < cutoff:
            recent.popleft()
        self._tenant_query_counts[tenant_id] += 1
        return self._tenant_query_counts[tenant_id], len(recent)

    def _should_sample(
        self,
        *,
        tenant_id: str | None,
        force_trace: bool,
    ) -> tuple[bool, str]:
        if force_trace:
            return True, "forced"
        if tenant_id is None:
            sample_rate = float(settings.trace_sample_rate)
            return random.random() < sample_rate, "default"

        total_count, hourly_count = self._record_tenant_query(tenant_id)
        if total_count <= int(settings.trace_new_tenant_threshold):
            return True, "new-tenant"

        if hourly_count > int(settings.trace_high_volume_threshold):
            sample_rate = float(settings.trace_high_volume_sample_rate)
            return random.random() < sample_rate, "high-volume"

        sample_rate = float(settings.trace_sample_rate)
        return random.random() < sample_rate, "default"

    def _materialize_trace(
        self,
        *,
        init_kwargs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        sampling_reason: str,
    ) -> TraceHandle | None:
        if self._client is None:
            return None
        merged_metadata = dict(init_kwargs.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        merged_metadata.setdefault("sampling_reason", sampling_reason)
        merged_tags = list(init_kwargs.get("tags") or [])
        if tags:
            merged_tags.extend(tags)
        trace_obj = _safe_construct(
            self._client.trace,
            name=init_kwargs["name"],
            session_id=init_kwargs["session_id"],
            user_id=init_kwargs.get("user_id"),
            metadata=merged_metadata,
            tags=merged_tags,
        )
        if trace_obj is None:
            return None
        return _LangfuseTrace(trace_obj)

    def begin_trace(
        self,
        *,
        name: str,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        force_trace: bool = False,
    ) -> TraceHandle:
        if self._client is None:
            return _NoOpTrace()
        sampled, sampling_reason = self._should_sample(
            tenant_id=tenant_id,
            force_trace=force_trace,
        )
        init_kwargs = {
            "name": name,
            "session_id": session_id,
            "user_id": user_id,
            "metadata": metadata,
            "tags": tags,
        }
        if sampled:
            materialized = self._materialize_trace(
                init_kwargs=init_kwargs,
                sampling_reason=sampling_reason,
            )
            if materialized is not None:
                return materialized
        return _DeferredTrace(
            self,
            init_kwargs=init_kwargs,
            sampled=sampled,
            sampling_reason=sampling_reason,
        )


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
    tenant_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    force_trace: bool = False,
) -> TraceHandle:
    return _service.begin_trace(
        name=name,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        metadata=metadata,
        tags=tags,
        force_trace=force_trace,
    )
