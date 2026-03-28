"""Tests for optional observability helpers."""

from __future__ import annotations

import sys
import types
import uuid

from backend.models import Document, DocumentStatus, DocumentType, Embedding
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
