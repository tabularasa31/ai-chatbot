"""Tests for optional observability helpers."""

from __future__ import annotations

import sys
import types
import uuid

from backend.models import Document, DocumentStatus, DocumentType, Embedding
from backend.observability import ObservabilityService
from backend.observability.formatters import (
    format_embedding_preview,
    format_query_embedding_preview,
    truncate_text,
)
from backend.observability.service import _safe_construct, _safe_invoke, get_observability


def test_truncate_text_keeps_short_input() -> None:
    assert truncate_text("short") == "short"


def test_truncate_text_shortens_long_input() -> None:
    text = "a" * 205
    assert truncate_text(text) == ("a" * 200) + "..."


def test_format_query_embedding_preview_limits_length() -> None:
    preview = format_query_embedding_preview([0.123456, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert preview == [0.1235, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_format_embedding_preview_uses_document_and_metadata() -> None:
    document = Document(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
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


def test_safe_construct_drops_unsupported_metadata_argument() -> None:
    def factory(*, name: str, input: dict[str, str]) -> dict[str, object]:
        return {"name": name, "input": input}

    result = _safe_construct(
        factory,
        name="vector-search",
        input={"query": "hello"},
        metadata={"tenant_id": "tenant-1"},
    )

    assert result == {
        "name": "vector-search",
        "input": {"query": "hello"},
    }


def test_safe_invoke_drops_unsupported_generation_end_arguments() -> None:
    received: dict[str, object] = {}

    def end(*, output: str) -> None:
        received["output"] = output

    _safe_invoke(
        end,
        output="done",
        metadata={"duration_ms": 12.3},
        usage={"input": 10, "output": 5},
        level="ERROR",
        status_message="boom",
    )

    assert received == {"output": "done"}


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


def test_sampling_skips_high_volume_until_promoted(monkeypatch) -> None:
    service = ObservabilityService()
    service._client = _FakeClient()
    service._enabled = True
    monkeypatch.setattr("backend.observability.service.settings.trace_new_tenant_threshold", 0)
    monkeypatch.setattr("backend.observability.service.settings.trace_high_volume_threshold", 1)
    monkeypatch.setattr("backend.observability.service.settings.trace_high_volume_sample_rate", 0.0)
    monkeypatch.setattr("backend.observability.service.settings.trace_sample_rate", 0.0)

    trace_a = service.begin_trace(
        name="rag-query",
        session_id="sess-a",
        tenant_id="tenant-1",
    )
    trace_b = service.begin_trace(
        name="rag-query",
        session_id="sess-b",
        tenant_id="tenant-1",
    )

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
    service = ObservabilityService()
    service._client = _FakeClient()
    service._enabled = True
    monkeypatch.setattr("backend.observability.service.settings.trace_new_tenant_threshold", 0)
    monkeypatch.setattr("backend.observability.service.settings.trace_sample_rate", 0.0)

    trace = service.begin_trace(
        name="rag-query",
        session_id="forced-session",
        tenant_id="tenant-2",
        force_trace=True,
    )

    assert trace.sampled is True
    assert len(service._client.traces) == 1
    assert service._client.traces[0].init_kwargs["metadata"]["sampling_reason"] == "forced"


def test_promote_updates_metadata_for_already_sampled_trace(monkeypatch) -> None:
    service = ObservabilityService()
    service._client = _FakeClient()
    service._enabled = True
    monkeypatch.setattr("backend.observability.service.settings.trace_new_tenant_threshold", 0)
    monkeypatch.setattr("backend.observability.service.settings.trace_sample_rate", 1.0)

    trace = service.begin_trace(
        name="rag-query",
        session_id="sampled-session",
        tenant_id="tenant-3",
    )
    trace.promote(metadata={"promotion_reason": "low_reliability_or_escalation"})

    assert len(service._client.traces) == 1
    assert service._client.traces[0].updates == [
        {"metadata": {"promotion_reason": "low_reliability_or_escalation"}}
    ]


def test_deferred_span_end_is_idempotent(monkeypatch) -> None:
    service = ObservabilityService()
    service._client = _FakeClient()
    service._enabled = True
    monkeypatch.setattr("backend.observability.service.settings.trace_sample_rate", 0.0)
    deferred = service.begin_trace(
        name="rag-query",
        session_id="deferred-session",
    )

    span = deferred.span(name="vector-search", input={"query": "hello"})
    span.end(output={"chunks": []})
    span.end(output={"chunks": ["duplicate"]})

    assert len(deferred._operations) == 1
