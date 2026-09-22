"""Tests for optional observability helpers."""

from __future__ import annotations


import logging
import sys
import types
import uuid

import pytest

from backend.models import Document, DocumentStatus, DocumentType, Embedding
from backend.observability import ObservabilityService
from backend.observability.formatters import (
    format_embedding_preview,
    format_query_embedding_preview,
    truncate_text,
)
from backend.observability.service import (
    _DEFERRED_OPS_MAXLEN,
    _DeferredGeneration,
    _DeferredSpan,
    _DeferredTrace,
    _LangfuseGeneration,
    _LangfuseSpan,
    _NoOpSpan,
    _safe_construct,
    _safe_invoke,
    get_observability,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("short", "short"),
        ("a" * 205, ("a" * 200) + "..."),
    ],
    ids=["short-input-unchanged", "long-input-truncated"],
)
def test_truncate_text(text, expected) -> None:
    assert truncate_text(text) == expected


def test_format_query_embedding_preview_limits_length() -> None:
    preview = format_query_embedding_preview([0.123456, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert preview == [0.1235, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_format_embedding_preview_uses_document_and_metadata() -> None:
    document = Document(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        filename="Guide",
        source_url="https://example.com/guide",
        file_type=DocumentType.url,
        status=DocumentStatus.ready,
    )
    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=document.id,
        document=document,
        chunk_text="chunk body",
        metadata_json={
            "chunk_index": 2,
            "page_title": "Guide page",
            "section_title": "Install",
        },
    )

    payload = format_embedding_preview(
        embedding,
        score=0.98765,
        score_name="similarity_score",
    )

    assert payload == {
        "id": str(embedding.id),
        "document_id": str(document.id),
        "source_url": "https://example.com/guide",
        "page_title": "Guide page",
        "section_title": "Install",
        "chunk_index": 2,
        "text_preview": "chunk body",
        "similarity_score": 0.9877,
    }


def test_observability_noops_when_config_missing(monkeypatch) -> None:
    service = get_observability()
    service._client = None
    service._enabled = False
    monkeypatch.setattr("backend.observability.service.settings.langfuse_host", None)
    monkeypatch.setattr("backend.observability.service.settings.langfuse_public_key", None)
    monkeypatch.setattr("backend.observability.service.settings.langfuse_secret_key", None)

    service.init()
    trace = service.begin_trace(
        name="rag-query",
        session_id="sess-1",
        metadata={"tenant_id": "tenant-1"},
    )

    trace.span(name="vector-search", input={"query": "hello"}).end(output={"chunks": []})
    trace.update(output={"answer": "hi"})

    assert service.enabled is False


def test_observability_can_reinit_after_shutdown(monkeypatch) -> None:
    class FakeLangfuse:
        instances = 0

        def __init__(self, **kwargs) -> None:
            type(self).instances += 1
            self.kwargs = kwargs
            self.flushed = False

        def flush(self) -> None:
            self.flushed = True

    service = get_observability()
    service._client = None
    service._enabled = False
    monkeypatch.setattr("backend.observability.service.settings.langfuse_host", "https://langfuse.test")
    monkeypatch.setattr("backend.observability.service.settings.langfuse_public_key", "pk-test")
    monkeypatch.setattr("backend.observability.service.settings.langfuse_secret_key", "sk-test")
    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FakeLangfuse))

    service.init()
    first_client = service._client

    assert service.enabled is True
    assert first_client is not None

    service.shutdown()

    assert first_client.flushed is True
    assert service._client is None
    assert service.enabled is False

    service.init()

    assert service.enabled is True
    assert service._client is not None
    assert service._client is not first_client
    assert FakeLangfuse.instances == 2

    service.shutdown()


@pytest.mark.parametrize(
    "invoke,kwargs,expected",
    [
        (
            _safe_construct,
            {"name": "vector-search", "input": {"query": "hello"}, "metadata": {"tenant_id": "tenant-1"}},
            {"name": "vector-search", "input": {"query": "hello"}},
        ),
        (
            _safe_invoke,
            {
                "output": "done",
                "metadata": {"duration_ms": 12.3},
                "usage": {"input": 10, "output": 5},
                "level": "ERROR",
                "status_message": "boom",
            },
            {"output": "done"},
        ),
    ],
    ids=["safe_construct-drops-unsupported", "safe_invoke-drops-unsupported"],
)
def test_safe_helpers_drop_unsupported_kwargs(invoke, kwargs, expected) -> None:
    received: dict[str, object] = {}

    if invoke is _safe_construct:
        def factory(*, name: str, input: dict[str, str]) -> dict[str, object]:
            return {"name": name, "input": input}

        assert invoke(factory, **kwargs) == expected
    else:
        def end(*, output: str) -> None:
            received["output"] = output

        invoke(end, **kwargs)
        assert received == expected


class _FakeSpan:
    def __init__(self) -> None:
        self.ended_with = None

    def end(self, **kwargs):
        self.ended_with = kwargs


class _FakeTrace:
    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.spans = []
        self.generations = []
        self.updates = []

    def span(self, **kwargs):
        span = _FakeSpan()
        self.spans.append((kwargs, span))
        return span

    def generation(self, **kwargs):
        generation = _FakeSpan()
        self.generations.append((kwargs, generation))
        return generation

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _FakeClient:
    def __init__(self) -> None:
        self.traces = []

    def trace(self, **kwargs):
        trace = _FakeTrace(**kwargs)
        self.traces.append(trace)
        return trace

    def flush(self):
        return None


def _sampling_service(
    monkeypatch,
    *,
    full_capture: bool = False,
    new_tenant_threshold: int = 0,
    high_volume_threshold: int = 1,
    high_volume_sample_rate: float = 0.0,
    sample_rate: float = 0.0,
) -> ObservabilityService:
    service = ObservabilityService()
    service._client = _FakeClient()
    service._enabled = True
    monkeypatch.setattr("backend.observability.service.settings.full_capture_mode", full_capture)
    monkeypatch.setattr("backend.observability.service.settings.trace_new_tenant_threshold", new_tenant_threshold)
    monkeypatch.setattr("backend.observability.service.settings.trace_high_volume_threshold", high_volume_threshold)
    monkeypatch.setattr(
        "backend.observability.service.settings.trace_high_volume_sample_rate", high_volume_sample_rate
    )
    monkeypatch.setattr("backend.observability.service.settings.trace_sample_rate", sample_rate)
    return service


def test_sampling_skips_high_volume_until_promoted(monkeypatch) -> None:
    service = _sampling_service(monkeypatch)

    trace_a = service.begin_trace(name="rag-query", session_id="sess-a", tenant_id="tenant-1")
    trace_b = service.begin_trace(name="rag-query", session_id="sess-b", tenant_id="tenant-1")

    assert trace_a.sampled is False
    assert trace_b.sampled is False

    trace_b.span(name="vector-search", input={"query": "hello"}).end(output={"chunks": []})
    trace_b.update(output={"answer": "hi"})
    trace_b.promote(metadata={"promotion_reason": "test"})

    assert len(service._client.traces) == 1
    materialized = service._client.traces[0]
    assert materialized.init_kwargs["session_id"] == "sess-b"
    assert materialized.init_kwargs["metadata"]["promotion_reason"] == "test"
    assert materialized.init_kwargs["metadata"]["sampling_reason"] == "high-volume"
    assert materialized.spans[0][0]["name"] == "vector-search"
    assert materialized.updates[0]["output"] == {"answer": "hi"}


def test_force_trace_bypasses_sampling(monkeypatch) -> None:
    service = _sampling_service(monkeypatch, sample_rate=0.0)

    trace = service.begin_trace(
        name="rag-query",
        session_id="forced-session",
        tenant_id="tenant-2",
        force_trace=True,
    )

    assert trace.sampled is True
    assert len(service._client.traces) == 1
    assert service._client.traces[0].init_kwargs["metadata"]["sampling_reason"] == "forced"


def test_full_capture_mode_skips_adaptive_sampling(monkeypatch) -> None:
    service = _sampling_service(monkeypatch, full_capture=True)

    trace_a = service.begin_trace(name="rag-query", session_id="sess-a", tenant_id="tenant-full")
    trace_b = service.begin_trace(name="rag-query", session_id="sess-b", tenant_id="tenant-full")

    assert trace_a.sampled is True
    assert trace_b.sampled is True
    assert len(service._client.traces) == 2
    for fake_trace in service._client.traces:
        meta = fake_trace.init_kwargs["metadata"]
        assert meta["sampling_reason"] == "full_capture"
        assert meta["sampling_mode"] == "full_capture"
        assert "sampling_mode:full_capture" in fake_trace.init_kwargs["tags"]


def test_full_capture_mode_still_advances_client_counters(monkeypatch) -> None:
    service = _sampling_service(monkeypatch, full_capture=True)

    service.begin_trace(name="rag-query", session_id="s1", tenant_id="tenant-counter")
    service.begin_trace(name="rag-query", session_id="s2", tenant_id="tenant-counter")

    assert service._client_query_counts["tenant-counter"] == 2


def test_sampled_trace_update_merges_existing_tags(monkeypatch) -> None:
    service = _sampling_service(monkeypatch, sample_rate=1.0)

    trace = service.begin_trace(
        name="rag-query",
        session_id="sampled-session",
        tenant_id="tenant-merge",
        tags=["tenant:tenant-merge"],
    )
    trace.update(output={"answer": "hi"}, tags=["variants:multi"])

    assert len(service._client.traces) == 1
    assert service._client.traces[0].updates[0]["tags"] == [
        "tenant:tenant-merge",
        "sampling_mode:adaptive",
        "variants:multi",
    ]


def test_deferred_trace_replays_variant_metadata_tags_and_query_embedding_span(monkeypatch) -> None:
    service = _sampling_service(monkeypatch)

    service.begin_trace(name="rag-query", session_id="seed-session", tenant_id="tenant-variants")
    trace = service.begin_trace(
        name="rag-query",
        session_id="deferred-session",
        tenant_id="tenant-variants",
        tags=["tenant:tenant-variants"],
    )

    trace.span(
        name="query-embedding",
        input={"query_variant_count": 3, "variant_mode": "multi"},
    ).end(
        output={
            "embedded_query_count": 3,
            "extra_embedded_queries": 2,
            "embedding_api_request_count": 1,
            "extra_embedding_api_requests": 0,
            "duration_ms": 4.2,
        }
    )
    trace.update(
        output={"result_count": 1},
        metadata={
            "variant_mode": "multi",
            "query_variant_count": 3,
            "extra_embedded_queries": 2,
            "extra_embedding_api_requests": 0,
            "extra_vector_search_calls": 2,
            "retrieval_duration_ms": 16.8,
        },
        tags=["variants:multi"],
    )
    trace.promote(metadata={"promotion_reason": "variant-observability-test"})

    assert len(service._client.traces) == 1
    materialized = service._client.traces[0]
    assert materialized.init_kwargs["session_id"] == "deferred-session"
    assert materialized.init_kwargs["metadata"]["promotion_reason"] == "variant-observability-test"
    assert materialized.spans[0][0]["name"] == "query-embedding"
    assert materialized.spans[0][1].ended_with["output"]["extra_embedded_queries"] == 2
    assert materialized.updates[0]["metadata"]["variant_mode"] == "multi"
    assert materialized.updates[0]["metadata"]["query_variant_count"] == 3
    assert materialized.updates[0]["tags"] == [
        "tenant:tenant-variants",
        "sampling_mode:adaptive",
        "variants:multi",
    ]


def test_deferred_trace_caps_operations_and_warns_on_drop(monkeypatch, caplog) -> None:
    """Deferred ops queue evicts oldest beyond maxlen and logs the drop exactly once."""
    service = _sampling_service(monkeypatch)

    service.begin_trace(name="rag-query", session_id="seed", tenant_id="cap-tenant")
    trace = service.begin_trace(name="rag-query", session_id="cap-session", tenant_id="cap-tenant")

    overflow = _DEFERRED_OPS_MAXLEN + 10
    for i in range(overflow):
        trace.span(name=f"span-{i}").end(output={"i": i})

    assert len(trace._operations) == _DEFERRED_OPS_MAXLEN
    assert trace._ops_added == overflow

    with caplog.at_level(logging.WARNING, logger="backend.observability.service"):
        trace.promote()

    materialized = service._client.traces[0]
    # only the last _DEFERRED_OPS_MAXLEN spans were kept (deque evicts oldest)
    assert len(materialized.spans) == _DEFERRED_OPS_MAXLEN
    assert materialized.spans[-1][0]["name"] == f"span-{overflow - 1}"
    assert any("dropped" in r.message for r in caplog.records)


def test_deferred_trace_logs_warning_when_materialize_fails(monkeypatch, caplog) -> None:
    class _BrokenClient:
        def trace(self, **kwargs):
            raise RuntimeError("langfuse unavailable")

        def flush(self):
            return None

    service = _sampling_service(monkeypatch)
    service._client = _BrokenClient()

    service.begin_trace(name="rag-query", session_id="seed", tenant_id="broken-tenant")
    trace = service.begin_trace(name="rag-query", session_id="broken-session", tenant_id="broken-tenant")

    trace.span(name="s-1").end()
    trace.span(name="s-2").end()
    trace.update(output="result")

    with caplog.at_level(logging.WARNING, logger="backend.observability.service"):
        trace.promote()

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("dropping" in m and "3" in m for m in warning_messages), (
        f"Expected a 'dropping 3 queued operations' warning, got: {warning_messages}"
    )

    # Second promote() must not re-log (operations were cleared)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="backend.observability.service"):
        trace.promote()
    assert not any("dropping" in r.message for r in caplog.records)


# ── update_metadata on span/generation handles ───────────────────────────────


class _RecordingSDKObservation:
    """Captures the kwargs an underlying Langfuse SDK update() would receive."""

    def __init__(self) -> None:
        self.update_calls: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.update_calls.append(dict(kwargs))

    def end(self, **kwargs: object) -> None:
        return None


@pytest.mark.parametrize(
    "handle_cls,attr,initial_metadata",
    [
        (_LangfuseSpan, "span_obj", {}),
        (_LangfuseSpan, "span_obj", {"tenant_id": "abc", "stage": "guard_relevance"}),
        (_LangfuseGeneration, "generation_obj", {}),
        (
            _LangfuseGeneration,
            "generation_obj",
            {"prompt_cache_prefix_tokens_estimate": 2048, "temperature": 0.2},
        ),
    ],
    ids=["span-no-initial", "span-preserves-initial", "generation-no-initial", "generation-preserves-initial"],
)
def test_update_metadata_merges_with_initial_metadata(handle_cls, attr, initial_metadata) -> None:
    """SDK update must receive the merged superset, not just the retry keys.

    Regression for the codex review concern: if the Langfuse SDK treats
    `metadata` updates as replacement (vs merge), stamping only retry fields
    would silently clobber the prompt/cache/model context attached at
    creation time.
    """
    sdk = _RecordingSDKObservation()
    handle = handle_cls(**{attr: sdk}, _metadata=dict(initial_metadata))

    handle.update_metadata(attempt_count=2, was_retried=True)

    assert sdk.update_calls == [
        {"metadata": {**initial_metadata, "attempt_count": 2, "was_retried": True}}
    ]


def test_langfuse_update_metadata_empty_kwargs_is_noop() -> None:
    sdk = _RecordingSDKObservation()
    span = _LangfuseSpan(span_obj=sdk)

    span.update_metadata()  # no kvs

    assert sdk.update_calls == []


def test_noop_span_update_metadata_does_nothing() -> None:
    # Must not raise — no underlying object to call.
    _NoOpSpan().update_metadata(attempt_count=1)


def test_langfuse_span_update_metadata_accumulates_across_calls() -> None:
    """Two successive updates each see the running merged superset."""
    sdk = _RecordingSDKObservation()
    span = _LangfuseSpan(span_obj=sdk, _metadata={"tenant_id": "abc"})

    span.update_metadata(attempt_count=1)
    span.update_metadata(retry_exhausted=True, retry_failure_kind="permanent")

    assert sdk.update_calls == [
        {"metadata": {"tenant_id": "abc", "attempt_count": 1}},
        {
            "metadata": {
                "tenant_id": "abc",
                "attempt_count": 1,
                "retry_exhausted": True,
                "retry_failure_kind": "permanent",
            }
        },
    ]


def test_langfuse_trace_span_factory_captures_initial_metadata() -> None:
    """The trace.span() factory must seed the handle's local metadata
    accumulator so update_metadata can later merge against it."""
    from backend.observability.service import _LangfuseTrace

    sdk_observation = _RecordingSDKObservation()

    class _FakeTraceObj:
        def span(self, **_kwargs):
            return sdk_observation

        def generation(self, **_kwargs):
            return sdk_observation

    trace = _LangfuseTrace(trace_obj=_FakeTraceObj(), tags=[])

    span = trace.span(
        name="guard_relevance",
        input=None,
        metadata={"tenant_id": "abc", "question_preview": "hi"},
    )
    span.update_metadata(attempt_count=1, was_retried=False)

    assert sdk_observation.update_calls == [
        {
            "metadata": {
                "tenant_id": "abc",
                "question_preview": "hi",
                "attempt_count": 1,
                "was_retried": False,
            }
        }
    ]


@pytest.mark.parametrize(
    "build_handle,expected_metadata",
    [
        (
            lambda trace: _DeferredSpan(
                trace,
                kind="span",
                kwargs={"name": "guard_relevance", "input": None, "metadata": {"tenant_id": "abc"}},
            ),
            {"tenant_id": "abc", "attempt_count": 2, "was_retried": True},
        ),
        (
            lambda trace: _DeferredGeneration(
                trace,
                kwargs={"name": "llm-generation", "model": "x", "input": None, "metadata": None},
            ),
            {"attempt_count": 1, "was_retried": False},
        ),
    ],
    ids=["deferred-span", "deferred-generation"],
)
def test_deferred_handle_update_metadata_merges_into_queued_kwargs(build_handle, expected_metadata) -> None:
    deferred_trace = _DeferredTrace.__new__(_DeferredTrace)
    recorded_ops: list[dict[str, object]] = []
    deferred_trace._record = recorded_ops.append  # type: ignore[attr-defined,method-assign]

    handle = build_handle(deferred_trace)
    handle.update_metadata(**{k: v for k, v in expected_metadata.items() if k != "tenant_id"})
    handle.end(output="ok")

    assert len(recorded_ops) == 1
    assert recorded_ops[0]["kwargs"]["metadata"] == expected_metadata
