"""Observability helpers: Langfuse tracing, PostHog metrics, Sentry errors."""

from backend.observability.metrics import (
    MetricsService,
    capture_event,
    get_metrics,
    group_identify,
    init_metrics,
    shutdown_metrics,
)
from backend.observability.sentry import init_sentry, shutdown_sentry
from backend.observability.service import (
    GenerationHandle,
    ObservabilityService,
    SpanHandle,
    TraceHandle,
    begin_trace,
    get_observability,
    init_observability,
    shutdown_observability,
)


def record_stage_ms(trace: TraceHandle | None, stage: str, duration_ms: float) -> None:
    """Best-effort wrapper around :meth:`TraceHandle.record_stage_ms`.

    Tolerates ``trace=None`` and ad-hoc duck-typed traces (test fakes that
    predate the method) by silently no-op'ing instead of raising. Use this
    helper at chat-pipeline call sites to keep the new diagnostic non-breaking
    for existing test doubles.
    """
    if trace is None:
        return
    fn = getattr(trace, "record_stage_ms", None)
    if fn is None:
        return
    fn(stage, duration_ms)


__all__ = [
    "GenerationHandle",
    "MetricsService",
    "ObservabilityService",
    "SpanHandle",
    "TraceHandle",
    "begin_trace",
    "capture_event",
    "get_metrics",
    "get_observability",
    "group_identify",
    "init_metrics",
    "init_observability",
    "init_sentry",
    "record_stage_ms",
    "shutdown_metrics",
    "shutdown_observability",
    "shutdown_sentry",
]
