"""Tests for the Langfuse→PostHog span/embedding/generation bridge.

These verify that `$ai_trace_id`, `$ai_parent_id`, and `$ai_span_id` are
propagated correctly on the three event types so PostHog can reconstruct
the span tree (`query-llm-traces-list` becomes non-empty).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.chat.events import (
    _emit_ai_embedding_event,
    _emit_ai_generation_event,
    _emit_ai_span_event,
)
from backend.observability.service import (
    _DeferredTrace,
    _LangfuseTrace,
    _NoOpTrace,
    ObservabilityService,
)


@pytest.fixture
def captured_events() -> list[dict[str, object]]:
    """Drop-in PostHog sink used by every test below."""
    return []


@pytest.fixture(autouse=True)
def patch_capture_event(captured_events: list[dict[str, object]]):
    def fake_capture(event: str, **kwargs: object) -> None:
        captured_events.append({"event": event, **kwargs})

    with patch("backend.chat.events.capture_event", side_effect=fake_capture):
        yield


def test_emit_ai_generation_includes_trace_ids(captured_events: list) -> None:
    _emit_ai_generation_event(
        tenant_public_id="ck_test",
        bot_public_id="bot_test",
        model="gpt-5-mini",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        latency_s=1.2,
        operation="chat/generate",
        trace_id="trace-abc",
        span_id="span-1",
        parent_id="trace-abc",
    )
    assert len(captured_events) == 1
    props = captured_events[0]["properties"]
    assert props["$ai_trace_id"] == "trace-abc"
    assert props["$ai_span_id"] == "span-1"
    assert props["$ai_parent_id"] == "trace-abc"
    assert props["$ai_model"] == "gpt-5-mini"


def test_emit_ai_generation_omits_trace_keys_when_absent(captured_events: list) -> None:
    """Back-compat: legacy call sites that don't pass trace_id still work."""
    _emit_ai_generation_event(
        tenant_public_id="ck_test",
        bot_public_id=None,
        model="gpt-5-mini",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
        latency_s=0.5,
        operation="chat/generate",
    )
    props = captured_events[0]["properties"]
    assert "$ai_trace_id" not in props
    assert "$ai_span_id" not in props
    assert "$ai_parent_id" not in props


def test_emit_ai_embedding_event_fires(captured_events: list) -> None:
    _emit_ai_embedding_event(
        tenant_public_id="ck_test",
        bot_public_id="bot_test",
        model="text-embedding-3-small",
        input_tokens=42,
        latency_s=0.08,
        operation="chat/embed",
        trace_id="trace-xyz",
        span_id="embed-span-1",
        parent_id="trace-xyz",
        input_count=2,
    )
    assert len(captured_events) == 1
    ev = captured_events[0]
    assert ev["event"] == "$ai_embedding"
    props = ev["properties"]
    assert props["$ai_provider"] == "openai"
    assert props["$ai_model"] == "text-embedding-3-small"
    assert props["$ai_input_tokens"] == 42
    assert props["$ai_latency"] == 0.08
    assert props["$ai_trace_id"] == "trace-xyz"
    assert props["$ai_span_id"] == "embed-span-1"
    assert props["$ai_parent_id"] == "trace-xyz"
    assert props["$ai_input_count"] == 2


def test_emit_ai_embedding_event_skipped_without_ids(captured_events: list) -> None:
    """No tenant/bot context → nothing captured."""
    _emit_ai_embedding_event(
        tenant_public_id=None,
        bot_public_id=None,
        model="text-embedding-3-small",
        input_tokens=10,
        latency_s=0.05,
        operation="chat/embed",
    )
    assert captured_events == []


def test_emit_ai_span_event_fires_with_extra_properties(captured_events: list) -> None:
    _emit_ai_span_event(
        tenant_public_id="ck_test",
        bot_public_id="bot_test",
        span_name="injection_guard",
        latency_s=0.003,
        trace_id="trace-abc",
        span_id="span-inj-1",
        parent_id="trace-abc",
        extra_properties={"detected": False, "level": "L1", "method": "structural"},
    )
    assert len(captured_events) == 1
    ev = captured_events[0]
    assert ev["event"] == "$ai_span"
    props = ev["properties"]
    assert props["$ai_span_name"] == "injection_guard"
    assert props["$ai_latency"] == 0.003
    assert props["$ai_trace_id"] == "trace-abc"
    assert props["$ai_parent_id"] == "trace-abc"
    assert props["detected"] is False
    assert props["level"] == "L1"
    assert props["method"] == "structural"


def test_emit_ai_span_event_skipped_without_trace_id(captured_events: list) -> None:
    """Span events without ``$ai_trace_id`` would be orphaned in PostHog — skip."""
    _emit_ai_span_event(
        tenant_public_id="ck_test",
        bot_public_id="bot_test",
        span_name="retrieval",
        latency_s=0.05,
        trace_id=None,
        span_id=None,
        parent_id=None,
    )
    assert captured_events == []


def test_emit_ai_span_extra_properties_cannot_override_core_keys(
    captured_events: list,
) -> None:
    """Caller-supplied ``extra_properties`` must not clobber `$ai_trace_id` etc."""
    _emit_ai_span_event(
        tenant_public_id="ck_test",
        bot_public_id="bot_test",
        span_name="retrieval",
        latency_s=0.05,
        trace_id="trace-real",
        span_id="span-real",
        parent_id="trace-real",
        extra_properties={"$ai_trace_id": "MALICIOUS", "chunk_count": 5},
    )
    props = captured_events[0]["properties"]
    assert props["$ai_trace_id"] == "trace-real"
    assert props["chunk_count"] == 5


# ---------------------------------------------------------------------------
# Observability service: trace_id propagation through TraceHandle subclasses
# ---------------------------------------------------------------------------


def test_noop_trace_has_no_posthog_trace_id() -> None:
    trace = _NoOpTrace()
    assert trace.posthog_trace_id is None
    span = trace.span(name="foo")
    assert span.posthog_trace_id is None
    assert span.posthog_span_id is None
    assert span.posthog_parent_id is None


def test_langfuse_trace_propagates_trace_id_to_spans() -> None:
    """Span/generation handles inherit ``posthog_trace_id`` from the trace."""
    class _FakeObs:
        def span(self, **kwargs: object) -> object:
            return object()

        def generation(self, **kwargs: object) -> object:
            return object()

    trace = _LangfuseTrace(
        trace_obj=_FakeObs(),
        tags=[],
        posthog_trace_id="trace-xyz",
    )
    span = trace.span(name="injection_l1")
    assert span.posthog_trace_id == "trace-xyz"
    assert span.posthog_parent_id == "trace-xyz"
    assert span.posthog_span_id is not None
    assert span.posthog_span_id != trace.posthog_trace_id

    generation = trace.generation(name="llm-generation", model="gpt-5")
    assert generation.posthog_trace_id == "trace-xyz"
    assert generation.posthog_parent_id == "trace-xyz"
    assert generation.posthog_span_id is not None


def test_deferred_trace_assigns_span_ids_under_same_trace_id() -> None:
    svc = ObservabilityService()
    trace = _DeferredTrace(
        svc,
        init_kwargs={
            "name": "chat",
            "session_id": "s",
            "user_id": None,
            "input": None,
            "metadata": None,
            "tags": None,
        },
        sampled=False,
        sampling_reason="default",
        posthog_trace_id="trace-deferred",
    )
    span_a = trace.span(name="a")
    span_b = trace.span(name="b")
    assert span_a.posthog_trace_id == "trace-deferred"
    assert span_b.posthog_trace_id == "trace-deferred"
    assert span_a.posthog_span_id != span_b.posthog_span_id
    assert span_a.posthog_parent_id == "trace-deferred"


def test_begin_trace_mints_unique_trace_ids() -> None:
    """Each ``begin_trace`` call gets its own ``posthog_trace_id`` UUID."""
    svc = ObservabilityService()
    # No Langfuse client → returns _NoOpTrace, which has None posthog_trace_id.
    trace_no = svc.begin_trace(name="t", session_id="s1")
    assert trace_no.posthog_trace_id is None

    # Force a fake client with a ``trace`` factory so begin_trace mints ids.
    class _FakeLangfuseClient:
        def trace(self, **kwargs: object) -> object:
            return object()

        def flush(self) -> None:
            return None

    svc._client = _FakeLangfuseClient()  # type: ignore[assignment]
    trace_a = svc.begin_trace(name="t", session_id="s1")
    trace_b = svc.begin_trace(name="t", session_id="s2")
    assert trace_a.posthog_trace_id is not None
    assert trace_b.posthog_trace_id is not None
    assert trace_a.posthog_trace_id != trace_b.posthog_trace_id
