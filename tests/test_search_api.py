"""Through-the-app tests for the /search API: auth, contract, error handling,
and the SQLite retrieval pipeline (including its trace/observability contract
and NER-concurrency behaviour).
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.search.service import (
    _async_rewrite_query_for_retrieval,
    async_embed_queries,
    async_embed_queries_with_stats,
)


@pytest.mark.asyncio
async def test_search_trace_pgvector_empty_path_records_vector_span(monkeypatch) -> None:
    from backend.search.service import search_similar_chunks_detailed_async

    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input: dict[str, object] | None = None
            self.output: dict[str, object] | None = None

        def end(self, **kwargs: object) -> None:
            self.output = kwargs["output"]

    class FakeTrace:
        def __init__(self) -> None:
            self.spans: list[FakeSpan] = []

        def span(self, **kwargs: object) -> FakeSpan:
            span = FakeSpan(kwargs["name"])
            span.input = kwargs["input"]
            self.spans.append(span)
            return span

    class FakeBind:
        url = "postgresql://test"

    class FakeDB:
        bind = FakeBind()

    async def fake_embed_queries(queries, **kwargs):
        return [[0.1] * 3 for _ in queries]

    monkeypatch.setattr("backend.search.service.async_embed_queries", fake_embed_queries)
    async def fake_pgvector_search(*args, **kwargs):
        return []

    monkeypatch.setattr("backend.search.service._async_pgvector_search", fake_pgvector_search)

    trace = FakeTrace()
    bundle = await search_similar_chunks_detailed_async(
        tenant_id=uuid.uuid4(),
        query="hello",
        top_k=3,
        db=FakeDB(),
        api_key="sk-test",
        trace=trace,
    )

    assert bundle.results == []
    assert bundle.variant_mode == "single"
    assert bundle.query_variant_count == 1
    assert bundle.extra_embedded_queries == 0
    assert bundle.extra_vector_search_calls == 0
    assert bundle.embedding_api_request_count == 1
    assert bundle.vector_search_call_count == 1
    assert [span.name for span in trace.spans] == [
        "query-expansion",
        "query-embedding",
        "vector-search",
    ]
    assert trace.spans[0].output == {
        "variants": ["hello"],
        "rewritten_variant": None,
        "query_variant_count": 1,
        "variant_mode": "single",
        "extra_variant_count": 0,
    }
    assert trace.spans[1].output == {
        "embedded_query_count": 1,
        "extra_embedded_queries": 0,
        "embedding_api_request_count": 1,
        "extra_embedding_api_requests": 0,
        "duration_ms": trace.spans[1].output["duration_ms"],
    }
    assert trace.spans[-1].output == {
        "chunks": [],
        "duration_ms": trace.spans[-1].output["duration_ms"],
        "total_candidates_scanned": 0,
        "vector_search_call_count": 1,
        "extra_vector_search_calls": 0,
    }


@pytest.mark.asyncio
async def test_search_trace_multi_variant_pgvector_reports_extra_work(monkeypatch) -> None:
    from backend.models import Embedding
    from backend.search.service import search_similar_chunks_detailed_async

    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input: dict[str, object] | None = None
            self.output: dict[str, object] | None = None

        def end(self, **kwargs: object) -> None:
            self.output = kwargs["output"]

    class FakeTrace:
        def __init__(self) -> None:
            self.spans: list[FakeSpan] = []

        def span(self, **kwargs: object) -> FakeSpan:
            span = FakeSpan(kwargs["name"])
            span.input = kwargs["input"]
            self.spans.append(span)
            return span

    class FakeBind:
        url = "postgresql://test"

    class FakeDB:
        bind = FakeBind()

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password instructions",
        metadata_json={"chunk_index": 0},
    )

    async def fake_embed_queries(queries, **kwargs):
        return [[0.1] * 3 for _ in queries]

    monkeypatch.setattr("backend.search.service.async_embed_queries", fake_embed_queries)
    async def fake_pgvector_search(*args, **kwargs):
        return [(embedding, 0.91)]

    monkeypatch.setattr("backend.search.service._async_pgvector_search", fake_pgvector_search)

    trace = FakeTrace()
    bundle = await search_similar_chunks_detailed_async(
        tenant_id=uuid.uuid4(),
        query="Reset-password!!   reset password",
        top_k=3,
        db=FakeDB(),
        api_key="sk-test",
        trace=trace,
    )

    query_embedding_span = next(span for span in trace.spans if span.name == "query-embedding")
    vector_span = next(span for span in trace.spans if span.name == "vector-search")

    assert bundle.query_variant_count == 3
    assert bundle.variant_mode == "multi"
    assert bundle.extra_variant_count == 2
    assert bundle.embedded_query_count == 3
    assert bundle.extra_embedded_queries == 2
    assert bundle.embedding_api_request_count == 1
    assert bundle.extra_embedding_api_requests == 0
    assert bundle.vector_search_call_count == 3
    assert bundle.extra_vector_search_calls == 2
    assert query_embedding_span.output == {
        "embedded_query_count": 3,
        "extra_embedded_queries": 2,
        "embedding_api_request_count": 1,
        "extra_embedding_api_requests": 0,
        "duration_ms": query_embedding_span.output["duration_ms"],
    }
    assert vector_span.output is not None
    assert vector_span.output["vector_search_call_count"] == 3
    assert vector_span.output["extra_vector_search_calls"] == 2
    assert bundle.retrieval_duration_ms >= bundle.query_embedding_duration_ms
    assert bundle.retrieval_duration_ms >= bundle.vector_search_duration_ms


def test_search_trace_sqlite_runs_full_stage_contract(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /search on SQLite runs every retrieval stage span, in order, with the
    expected input/output contract (query-expansion through source-overlap-check).

    Covers the same observability counters as the former
    test_search_sqlite_observability_counts_executed_variants (query_variant_count,
    vector_search_call_count, extra_vector_search_calls), asserted below via the
    vector-search span output instead of a direct bundle.
    """
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input: dict[str, object] | None = None
            self.output: dict[str, object] | None = None

        def end(self, **kwargs: object) -> None:
            self.output = kwargs["output"]

    class FakeTrace:
        def __init__(self) -> None:
            self.spans: list[FakeSpan] = []

        def span(self, **kwargs: object) -> FakeSpan:
            span = FakeSpan(kwargs["name"])
            span.input = kwargs["input"]
            self.spans.append(span)
            return span

        def update(self, **kwargs: object) -> None:
            return None

    token = register_and_verify_user(tenant, db_session, email="sqlite_trace@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "SQLite Trace Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="reset.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="reset password docs",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    db_session.add_all(
        [
            Embedding(
                document_id=doc.id,
                chunk_text="reset password instructions in account settings",
                vector=None,
                metadata_json={"chunk_index": 0, "vector": [1.0, 0.0, 0.0]},
            ),
            Embedding(
                document_id=doc.id,
                chunk_text="download billing invoice from dashboard",
                vector=None,
                metadata_json={"chunk_index": 1, "vector": [0.2, 0.9, 0.0]},
            ),
            Embedding(
                document_id=doc.id,
                chunk_text="rotate api key in workspace settings",
                vector=None,
                metadata_json={"chunk_index": 2, "vector": [0.1, 0.1, 0.9]},
            ),
        ]
    )
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[1.0, 0.0, 0.0])]

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.search.routes.begin_trace", lambda **kwargs: fake_trace)

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "Reset-password!!   reset password", "top_k": 2},
    )
    assert response.status_code == 200

    assert [span.name for span in fake_trace.spans] == [
        "query-expansion",
        "query-embedding",
        "vector-search",
        "bm25-search",
        # entity-overlap-search appears between bm25-search and rrf-fusion
        # whenever ENTITY_OVERLAP_ENABLED is on (default since Step 6 of
        # the entity-aware retrieval epic). NER inside the channel is
        # mocked at the OpenAI client level by the test fixture, so the
        # span fires even though no real entities are extracted.
        "entity-overlap-search",
        "rrf-fusion",
        "reranking",
        "script-boost",
        "mmr-pass",
        "source-overlap-check",
    ]

    vector_span = next(span for span in fake_trace.spans if span.name == "vector-search")
    bm25_span = next(span for span in fake_trace.spans if span.name == "bm25-search")
    overlap_span = next(span for span in fake_trace.spans if span.name == "source-overlap-check")
    assert vector_span.input is not None
    assert vector_span.input["engine"] == "python-cosine"
    assert vector_span.output is not None
    assert vector_span.output["vector_search_call_count"] == 3
    assert vector_span.output["extra_vector_search_calls"] == 2
    assert bm25_span.input is not None
    assert bm25_span.input["bm25_expansion_mode"] == "asymmetric"
    assert bm25_span.output is not None
    assert bm25_span.output["chunks"]
    assert bm25_span.output["bm25_query_variant_count"] == 1
    assert bm25_span.output["bm25_variant_eval_count"] == 1
    assert bm25_span.output["extra_bm25_variant_evals"] == 0
    assert overlap_span.output is not None
    assert overlap_span.output["contradiction_detected"] is False
    assert overlap_span.output["contradiction_count"] == 0
    assert overlap_span.output["contradiction_pair_count"] == 0
    assert overlap_span.output["contradiction_basis_types"] == []


def test_search_sqlite_deduplicates_variant_candidates_by_max_similarity(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """A chunk matched by more than one query variant keeps its best (max) vector
    similarity, and appears only once in the final results — not once per variant.

    ``shared`` scores weakly against the first query variant but strongly against
    the second; ``tertiary`` scores in between and is only matched by the third
    variant. If dedup kept a variant's first-seen score instead of the max,
    ``tertiary`` would incorrectly outrank ``shared`` in the final response.
    """
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="variant_dedup@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Variant Dedup Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="dedup.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="dedup docs",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    # Lexically neutral text (no overlap with the query) keeps BM25 out of the
    # ranking, so the final order reflects the vector-dedup decision alone.
    db_session.add_all(
        [
            Embedding(
                document_id=doc.id,
                chunk_text="alpha bravo charlie",
                vector=None,
                metadata_json={"chunk_index": 0, "vector": [1.0, 0.0, 0.0]},
            ),
            Embedding(
                document_id=doc.id,
                chunk_text="delta echo foxtrot",
                vector=None,
                metadata_json={"chunk_index": 1, "vector": [0.0, 0.0, 1.0]},
            ),
        ]
    )
    db_session.commit()

    # expand_query("Widget-setup!!   widget setup") yields 3 variants (raw,
    # punctuation-cleaned, deduped-tokens) — same trick as the query used in
    # test_search_trace_sqlite_runs_full_stage_contract.
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.4, 0.1, 0.0]),  # variant 1: weak match on "shared" (index 0)
        Mock(embedding=[1.0, 0.0, 0.0]),  # variant 2: strong match on "shared" (index 0)
        Mock(embedding=[0.0, 0.0, 1.0]),  # variant 3: match on "tertiary" (index 1)
    ]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "Widget-setup!!   widget setup", "top_k": 2},
    )
    assert response.status_code == 200
    data = response.json()["results"]

    result_texts = [item["chunk_text"] for item in data]
    assert len(result_texts) == len(set(result_texts))
    assert result_texts.index("alpha bravo charlie") < result_texts.index("delta echo foxtrot")


@pytest.mark.asyncio
async def test_search_trace_uses_script_bucket_naming_for_script_boost_and_mmr(
    monkeypatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import search_similar_chunks_detailed_async

    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input: dict[str, object] | None = None
            self.output: dict[str, object] | None = None

        def end(self, **kwargs: object) -> None:
            self.output = kwargs["output"]

    class FakeTrace:
        def __init__(self) -> None:
            self.spans: list[FakeSpan] = []

        def span(self, **kwargs: object) -> FakeSpan:
            span = FakeSpan(kwargs["name"])
            span.input = kwargs["input"]
            self.spans.append(span)
            return span

    class FakeBind:
        url = "postgresql://test"

    class FakeDB:
        bind = FakeBind()

    cyrillic_primary = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="сброс пароля в настройках аккаунта",
        metadata_json={"language": "ru", "chunk_index": 0},
    )
    cyrillic_duplicate = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="сброс пароля в настройках аккаунта сейчас",
        metadata_json={"language": "ru", "chunk_index": 1},
    )
    latin_diverse = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="download billing invoice from account page",
        metadata_json={"language": "en", "chunk_index": 2},
    )

    async def fake_embed_queries(queries, **kwargs):
        return [[0.1] * 3 for _ in queries]

    monkeypatch.setattr("backend.search.service.async_embed_queries", fake_embed_queries)
    async def fake_pgvector_search(*args, **kwargs):
        return [
            (cyrillic_primary, 0.95),
            (cyrillic_duplicate, 0.92),
            (latin_diverse, 0.7),
        ]

    monkeypatch.setattr("backend.search.service._async_pgvector_search", fake_pgvector_search)
    # FakeDB doesn't implement .execute(); pretend the tenant has embeddings
    # so the entity-overlap channel proceeds in this trace-contract test.
    async def fake_tenant_has_embeddings(*args, **kwargs):
        return True

    monkeypatch.setattr(
        "backend.search.service._async_tenant_has_embeddings",
        fake_tenant_has_embeddings,
    )

    trace = FakeTrace()
    bundle = await search_similar_chunks_detailed_async(
        tenant_id=uuid.uuid4(),
        query="как сбросить пароль",
        top_k=3,
        db=FakeDB(),
        api_key="sk-test",
        trace=trace,
    )

    assert bundle.query_script_bucket == "cyrillic"
    assert [span.name for span in trace.spans] == [
        "query-expansion",
        "query-embedding",
        "vector-search",
        "bm25-search",
        # entity-overlap-search lands here when ENTITY_OVERLAP_ENABLED is on
        # (default since Step 6).
        "entity-overlap-search",
        "rrf-fusion",
        "reranking",
        "script-boost",
        "mmr-pass",
        "source-overlap-check",
    ]

    script_span = next(span for span in trace.spans if span.name == "script-boost")
    mmr_span = next(span for span in trace.spans if span.name == "mmr-pass")
    legacy_query_key = "query" + "_language"
    legacy_boost_key = "language" + "_boost"
    serialized_trace = repr(
        [
            {"name": span.name, "input": span.input, "output": span.output}
            for span in trace.spans
        ]
    )

    assert script_span.input is not None
    assert script_span.input["query_script_bucket"] == "cyrillic"
    assert legacy_query_key not in script_span.input
    assert mmr_span.input is not None
    assert mmr_span.input["candidate_count"] == 3
    assert mmr_span.output is not None
    assert mmr_span.output["selection_diagnostics"]
    assert "query_script_bucket" in serialized_trace
    assert "script-boost" in serialized_trace
    assert "mmr-pass" in serialized_trace
    assert legacy_query_key not in serialized_trace
    assert legacy_boost_key not in serialized_trace


@pytest.mark.asyncio
async def test_embed_query_uses_openai_client(mock_openai_client: Mock) -> None:
    """async_embed_query calls OpenAI with correct model name."""
    from backend.search.service import async_embed_query

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    await async_embed_query("test query", api_key="sk-test")
    mock_openai_client.embeddings.create.assert_called_once()
    call_kwargs = mock_openai_client.embeddings.create.call_args
    assert call_kwargs.kwargs.get("model") == "text-embedding-3-small"
    assert call_kwargs.kwargs.get("input") == "test query"


@pytest.mark.asyncio
async def test_embed_queries_batches_variants_into_single_openai_call(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 3),
        Mock(embedding=[0.2] * 3),
    ]

    vectors = await async_embed_queries(["first", "second"], api_key="sk-test")

    assert vectors == [[0.1] * 3, [0.2] * 3]
    mock_openai_client.embeddings.create.assert_called_once()
    call_kwargs = mock_openai_client.embeddings.create.call_args
    assert call_kwargs.kwargs.get("input") == ["first", "second"]


@pytest.mark.asyncio
async def test_embed_queries_with_stats_reports_actual_request_count(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 3),
        Mock(embedding=[0.2] * 3),
    ]

    vectors, request_count = await async_embed_queries_with_stats(
        ["first", "second"],
        api_key="sk-test",
    )

    assert vectors == [[0.1] * 3, [0.2] * 3]
    assert request_count == 1
    mock_openai_client.embeddings.create.assert_called_once()


def test_search_no_embeddings(
    mock_openai_client: Mock, tenant: TestClient, db_session: Session
) -> None:
    """Given no embeddings in DB, POST /search → returns empty results list."""
    token = register_and_verify_user(tenant, db_session, email="noemb@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Emb Tenant"},
    )
    set_client_openai_key(tenant, token)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []


def test_search_route_traces_variant_summary(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.search.service import SearchResultBundle

    class FakeTrace:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, object]] = []

        def span(self, **kwargs: object):
            class FakeSpan:
                def end(self, **kwargs: object) -> None:
                    return None

            return FakeSpan()

        def update(self, **kwargs: object) -> None:
            self.update_calls.append(kwargs)

    token = register_and_verify_user(tenant, db_session, email="trace-search@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Search Tenant"},
    )
    set_client_openai_key(tenant, token)

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.search.routes.begin_trace", lambda **kwargs: fake_trace)
    from unittest.mock import AsyncMock

    _bundle = SearchResultBundle(
        results=[],
        query_variant_count=3,
        variant_mode="multi",
        extra_variant_count=2,
        embedded_query_count=3,
        extra_embedded_queries=2,
        embedding_api_request_count=1,
        extra_embedding_api_requests=0,
        vector_search_call_count=3,
        extra_vector_search_calls=2,
        bm25_expansion_mode="symmetric_variants",
        bm25_query_variant_count=2,
        bm25_variant_eval_count=2,
        extra_bm25_variant_evals=1,
        bm25_merged_hit_count_before_cap=4,
        bm25_merged_hit_count_after_cap=3,
        retrieval_duration_ms=12.5,
        query_embedding_duration_ms=2.5,
        vector_search_duration_ms=7.5,
    )
    monkeypatch.setattr(
        "backend.search.routes.search_similar_chunks_detailed_async",
        AsyncMock(return_value=_bundle),
    )

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "Reset-password!!   reset password", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}
    metadata = fake_trace.update_calls[-1]["metadata"]
    assert metadata["reliability"] == {
        "base_score": "low",
        "score": "low",
        "cap": None,
        "cap_reason": None,
        "signals": [{"kind": "weak_recall"}],
        "evidence": {},
    }
    assert metadata["source_overlap_detected"] is False
    assert metadata["source_overlap_pairs"] == []
    assert metadata["contradiction_detected"] is False
    assert metadata["contradiction_count"] == 0
    assert metadata["contradiction_pair_count"] == 0
    assert metadata["contradiction_basis_types"] == []
    assert fake_trace.update_calls == [
        {
            "output": {"result_count": 0},
            "metadata": {
                "route": "/search",
                "search_result_count": 0,
                "reliability": {
                    "base_score": "low",
                    "score": "low",
                    "cap": None,
                    "cap_reason": None,
                    "signals": [{"kind": "weak_recall"}],
                    "evidence": {},
                },
                "source_overlap_detected": False,
                "source_overlap_pairs": [],
                "contradiction_detected": False,
                "contradiction_count": 0,
                "contradiction_pair_count": 0,
                "contradiction_basis_types": [],
                "contradiction_adjudication_enabled": False,
                "contradiction_adjudication_applied_to_any_fact": False,
                "contradiction_adjudication_status": "disabled",
                "contradiction_adjudication_candidate_count": 0,
                "contradiction_adjudication_sent_count": 0,
                "contradiction_adjudication_completed_count": 0,
                "contradiction_adjudication_confirmed_count": 0,
                "contradiction_adjudication_rejected_count": 0,
                "contradiction_adjudication_inconclusive_count": 0,
                "contradiction_adjudication_error_count": 0,
                "variant_mode": "multi",
                "query_variant_count": 3,
                "extra_embedded_queries": 2,
                "extra_embedding_api_requests": 0,
                "extra_vector_search_calls": 2,
                "bm25_expansion_mode": "symmetric_variants",
                "bm25_query_variant_count": 2,
                "bm25_variant_eval_count": 2,
                "extra_bm25_variant_evals": 1,
                "bm25_merged_hit_count_before_cap": 4,
                "bm25_merged_hit_count_after_cap": 3,
                "retrieval_duration_ms": 12.5,
            },
            "tags": ["variants:multi"],
        }
    ]


def test_search_single_embedding_match(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Create user, tenant, document, embedding; mock the embedding call to return a similar vector."""
    vec = [0.1] * 1536
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=vec)]

    token = register_and_verify_user(tenant, db_session, email="single@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Single Tenant"},
    )
    set_client_openai_key(tenant, token)
    md_content = b"# Doc\n\nRelevant content here."
    upload_resp = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("doc.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]
    tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "relevant content", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["document_id"] == doc_id
    assert data["results"][0]["similarity"] > 0.0
    assert "Relevant content" in data["results"][0]["chunk_text"]


def test_search_multiple_results_sorted(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """3 embeddings with different similarity scores; results sorted DESC by similarity."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="multi@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Multi Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="multi.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="chunk0 chunk1 chunk2",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    # Vectors in different directions for distinct similarity scores
    # query: [1,0,0,...]; high: same direction; mid: 45°; low: orthogonal
    query_vec = [1.0] + [0.0] * 1535
    high_vec = [0.99, 0.1] + [0.0] * 1534
    mid_vec = [0.5, 0.5] + [0.0] * 1534
    low_vec = [0.0, 1.0] + [0.0] * 1534

    for i, v in enumerate([high_vec, mid_vec, low_vec]):
        emb = Embedding(
            document_id=doc.id,
            chunk_text=f"chunk{i}",
            vector=None,
            metadata_json={"chunk_index": i, "vector": v},
        )
        db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "search", "top_k": 3},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 3
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True)
    assert sims[0] > sims[1] > sims[2]


def test_search_respects_top_k(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Have > top_k embeddings, request top_k=2, only 2 results returned."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="topk@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "TopK Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="topk.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="a b c d e",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    vec = [0.1] * 1536
    for i in range(5):
        emb = Embedding(
            document_id=doc.id,
            chunk_text=f"chunk{i}",
            vector=None,
            metadata_json={"chunk_index": i, "vector": vec},
        )
        db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=vec)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "x", "top_k": 2},
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


def test_search_other_client_isolated(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Create embeddings for tenant A and B; search as user A → only A's results."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token_a = register_and_verify_user(tenant, db_session, email="isol_a@example.com")
    cl_a_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Tenant A"},
    )
    set_client_openai_key(tenant, token_a)
    client_a_id = uuid.UUID(cl_a_resp.json()["id"])

    token_b = register_and_verify_user(tenant, db_session, email="isol_b@example.com")
    cl_b_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )
    client_b_id = uuid.UUID(cl_b_resp.json()["id"])

    doc_a = Document(
        tenant_id=client_a_id,
        filename="a.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Tenant A secret",
    )
    doc_b = Document(
        tenant_id=client_b_id,
        filename="b.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Tenant B secret",
    )
    db_session.add_all([doc_a, doc_b])
    db_session.commit()
    db_session.refresh(doc_a)
    db_session.refresh(doc_b)

    vec = [0.1] * 1536
    emb_a = Embedding(
        document_id=doc_a.id,
        chunk_text="Tenant A secret",
        vector=None,
        metadata_json={"chunk_index": 0, "vector": vec},
    )
    emb_b = Embedding(
        document_id=doc_b.id,
        chunk_text="Tenant B secret",
        vector=None,
        metadata_json={"chunk_index": 0, "vector": vec},
    )
    db_session.add_all([emb_a, emb_b])
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=vec)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"query": "secret", "top_k": 5},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["document_id"] == str(doc_a.id)
    assert "Tenant A" in results[0]["chunk_text"]


def test_search_requires_auth(tenant: TestClient) -> None:
    """No JWT → 401."""
    response = tenant.post(
        "/search",
        json={"query": "test", "top_k": 3},
    )
    assert response.status_code == 401


def test_search_requires_client(tenant: TestClient, db_session: Session) -> None:
    """Auth user without a tenant → 404."""
    token = register_and_verify_user(tenant, db_session, email="noclient@example.com")
    # Do NOT create a tenant

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test", "top_k": 3},
    )
    assert response.status_code == 404


def test_search_invalid_top_k(tenant: TestClient, db_session: Session) -> None:
    """top_k <= 0 → 422."""
    token = register_and_verify_user(tenant, db_session, email="invalid@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Invalid Tenant"},
    )
    set_client_openai_key(tenant, token)

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test", "top_k": 0},
    )
    assert response.status_code == 422


def test_search_empty_query_rejected(
    tenant: TestClient, db_session: Session
) -> None:
    """Empty query → 422."""
    token = register_and_verify_user(tenant, db_session, email="emptyq@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Tenant"},
    )
    set_client_openai_key(tenant, token)

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "", "top_k": 3},
    )
    assert response.status_code == 422


def test_search_default_top_k(
    mock_openai_client: Mock, tenant: TestClient, db_session: Session
) -> None:
    """Omit top_k → defaults to 3."""
    token = register_and_verify_user(tenant, db_session, email="default@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Default Tenant"},
    )
    set_client_openai_key(tenant, token)
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test"},
    )
    assert response.status_code == 200
    assert "results" in response.json()


# --- BM25 search unit tests ---


@pytest.mark.rag_edge
def test_search_low_vector_similarity_still_returns_chunk(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """SQLite shared pipeline still returns lexical matches even with zero vector confidence."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="fallback@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fallback Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="cors.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="CORS configuration docs",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    # Orthogonal vector: cosine sim with [1,0,0,...] will be 0
    low_vec = [0.0, 1.0] + [0.0] * 1534
    emb = Embedding(
        document_id=doc.id,
        chunk_text="CORS settings: allow_origins controls cross-origin requests",
        vector=None,
        metadata_json={"chunk_index": 0, "vector": low_vec},
    )
    db_session.add(emb)
    db_session.commit()

    # Query vector orthogonal to stored → vector confidence 0, lexical stage must still help.
    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "cors", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert "CORS" in data["results"][0]["chunk_text"]
    assert data["results"][0]["document_id"] == str(doc.id)
    assert data["results"][0]["similarity"] > 0.0


def test_search_sqlite_hybrid_pipeline_allows_lexical_signal_to_outrank_purer_cosine(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="sqlitehybrid@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "SQLite Hybrid Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="hybrid.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="hybrid retrieval docs",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    db_session.add_all(
        [
            Embedding(
                document_id=doc.id,
                chunk_text="unrelated words xyz qrs",
                vector=None,
                metadata_json={"chunk_index": 0, "vector": [1.0, 0.0] + [0.0] * 1534},
            ),
            Embedding(
                document_id=doc.id,
                chunk_text="cors configuration settings",
                vector=None,
                metadata_json={"chunk_index": 1, "vector": [0.9, 0.1] + [0.0] * 1534},
            ),
        ]
    )
    db_session.commit()

    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "cors configuration", "top_k": 2},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert "cors configuration settings" in [item["chunk_text"] for item in results]
    assert results[0]["chunk_text"] == "cors configuration settings"


@pytest.mark.rag_edge
def test_search_openai_unavailable_returns_503(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from openai import APIError

    token = register_and_verify_user(tenant, db_session, email="search503@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Search 503 Tenant"},
    )
    set_client_openai_key(tenant, token)
    mock_openai_client.embeddings.create.side_effect = APIError(
        "Service unavailable",
        request=Mock(),
        body=None,
    )

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "hello", "top_k": 3},
    )
    assert response.status_code == 503


def test_search_openai_timeout_returns_503(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from openai import APITimeoutError

    token = register_and_verify_user(tenant, db_session, email="search-timeout@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Search Timeout Tenant"},
    )
    set_client_openai_key(tenant, token)
    mock_openai_client.embeddings.create.side_effect = APITimeoutError(request=Mock())

    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "hello", "top_k": 3},
    )
    assert response.status_code == 503


@pytest.mark.rag_edge
def test_search_skips_malformed_metadata_vectors(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="malformedvec@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Malformed Vec Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="badvec.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    db_session.add_all(
        [
            Embedding(
                document_id=doc.id,
                chunk_text="bad vector string",
                vector=None,
                metadata_json={"chunk_index": 0, "vector": "not-a-list"},
            ),
            Embedding(
                document_id=doc.id,
                chunk_text="bad vector empty",
                vector=None,
                metadata_json={"chunk_index": 1, "vector": []},
            ),
        ]
    )
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "content", "top_k": 5},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


@pytest.mark.rag_edge
def test_search_skips_vector_with_wrong_dimension(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="wrongdim@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Wrong Dim Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="wrongdim.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="wrong dim chunk",
        vector=None,
        metadata_json={"chunk_index": 0, "vector": [0.1] * 10},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    response = tenant.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything", "top_k": 3},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


# --- Unit tests for cross-lingual query expansion ---


@pytest.mark.asyncio
async def test_rewrite_query_for_retrieval_returns_rewritten_query() -> None:
    """Happy path: LLM returns a documentation-style keyword phrase in the same language."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "определение языка мультиязычная поддержка"

    with patch("backend.search.service.get_async_openai_client") as mock_client_factory, patch(
        "backend.search.service.async_call_openai_with_retry",
        new=AsyncMock(return_value=mock_response),
    ):
        mock_client_factory.return_value = MagicMock()
        result = await _async_rewrite_query_for_retrieval(
            "Почему бот не отвечает на русском?", api_key="test-key"
        )

    assert result == "определение языка мультиязычная поддержка"


@pytest.mark.asyncio
async def test_rewrite_query_for_retrieval_returns_none_on_error() -> None:
    """Rewrite failures degrade gracefully — None means caller skips the variant."""
    from unittest.mock import AsyncMock, MagicMock, patch

    with patch("backend.search.service.get_async_openai_client") as mock_client_factory, patch(
        "backend.search.service.async_call_openai_with_retry",
        new=AsyncMock(side_effect=RuntimeError("timeout")),
    ):
        mock_client_factory.return_value = MagicMock()
        result = await _async_rewrite_query_for_retrieval(
            "Почему бот не отвечает на русском?", api_key="test-key"
        )

    assert result is None


@pytest.mark.asyncio
async def test_rewrite_query_for_retrieval_returns_none_on_empty_response() -> None:
    """Empty LLM output does not yield a blank variant."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_response = MagicMock()
    mock_response.choices[0].message.content = ""

    with patch("backend.search.service.get_async_openai_client") as mock_client_factory, patch(
        "backend.search.service.async_call_openai_with_retry",
        new=AsyncMock(return_value=mock_response),
    ):
        mock_client_factory.return_value = MagicMock()
        result = await _async_rewrite_query_for_retrieval("", api_key="test-key")

    assert result is None


@pytest.mark.asyncio
async def test_entity_ner_runs_concurrently_with_vector_and_bm25(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, async_search_session
) -> None:
    """NER must run in parallel with vector + BM25, not sequentially.

    Ground truth from the prod eval (see ClickUp 86exe5pjx): sequential
    NER added ~+8s p50 to chat-turn latency. Parallelization should hide
    NER behind the existing vector + BM25 budget — total stage latency
    should approximate max(NER, vector+bm25), not their sum.

    Test uses sleep-based mocks: NER takes 0.3s, vector search takes
    0.3s. Sequential would be ~0.6s; parallel should land near 0.3s.
    Threshold of 0.5s gives margin for thread spin-up / scheduling
    jitter while still failing if NER is back to sequential.
    """
    import asyncio
    import time as _time

    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.service import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="parallel_ner@example.com")
    tenant_id = _create_client(db_session, user, name="Parallel NER").id
    doc = Document(
        tenant_id=tenant_id,
        filename="x.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="x",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="hello world chunk",
            vector=None,
            metadata_json={"chunk_index": 0, "vector": [1.0, 0.0, 0.0]},
        )
    )
    db_session.commit()

    async def fake_embed_queries(queries, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in queries]

    monkeypatch.setattr("backend.search.service.async_embed_queries", fake_embed_queries)

    # Make the vector candidate build "slow" so NER has time to run in parallel.
    real_build = __import__(
        "backend.search.service", fromlist=["_async_build_vector_candidate_set"]
    )._async_build_vector_candidate_set

    async def slow_vector_build(*args, **kwargs):
        await asyncio.sleep(0.3)
        return await real_build(*args, **kwargs)

    monkeypatch.setattr(
        "backend.search.service._async_build_vector_candidate_set", slow_vector_build
    )

    def slow_ner(query, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        _time.sleep(0.3)
        return ["hello"]

    monkeypatch.setattr(
        "backend.search.service.extract_entities_from_query", slow_ner
    )

    started = _time.perf_counter()
    await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="hello world",
        top_k=3,
        db=async_search_session,
        api_key="sk-test",
    )
    elapsed = _time.perf_counter() - started

    # Sequential would take ~0.6s. Parallel should be ~0.3s (plus a bit
    # of overhead for the rest of the pipeline). Anything <0.5s confirms
    # NER and vector built ran concurrently.
    assert elapsed < 0.5, (
        f"Stage took {elapsed:.3f}s — looks sequential. "
        f"Expected <0.5s when NER (0.3s) overlaps vector (0.3s)."
    )


@pytest.mark.asyncio
async def test_entity_ner_skipped_for_tenant_with_no_embeddings(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, async_search_session
) -> None:
    """Tenants with zero indexed chunks must not pay for a NER call.

    Codex P1 fix on PR #544: ``future.cancel()`` cannot stop a thread
    that already started running. Submitting NER unconditionally and
    then "cancelling" on the empty-vector early-return still pays for
    the OpenAI call. Pre-check via _async_tenant_has_embeddings gates the
    submission so freshly-onboarded / empty-FAQ tenants pay zero.
    """
    from backend.search.service import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="no_emb@example.com")
    tenant_id = _create_client(db_session, user, name="No Embeddings").id
    # No documents, no embeddings — tenant exists but has nothing indexed.

    async def fake_embed_queries(queries, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in queries]

    monkeypatch.setattr("backend.search.service.async_embed_queries", fake_embed_queries)

    ner_calls: list[str] = []

    def tracking_ner(query, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        ner_calls.append(query)
        return ["should_not_be_called"]

    monkeypatch.setattr(
        "backend.search.service.extract_entities_from_query", tracking_ner
    )

    await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="anything goes here",
        top_k=3,
        db=async_search_session,
        api_key="sk-test",
    )

    assert ner_calls == [], (
        f"NER must not be called for tenants with no embeddings; "
        f"got {len(ner_calls)} call(s): {ner_calls}"
    )


@pytest.mark.asyncio
async def test_entity_ner_future_cancelled_on_empty_vector_path(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, async_search_session
) -> None:
    """Empty vector candidates → NER future is cancelled, executor shut down.

    Otherwise a NER call that's still in-flight at early-return time
    keeps a thread + an OpenAI request alive for nothing.
    """
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.service import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="empty_path_ner@example.com")
    tenant_id = _create_client(db_session, user, name="Empty NER").id
    # Document with no embeddings → vector_candidates is empty → early return.
    doc = Document(
        tenant_id=tenant_id,
        filename="empty.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="empty",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="text that won't match the query script",
            vector=None,
            metadata_json={"chunk_index": 0, "vector": [0.0, 0.0, 0.0]},
        )
    )
    db_session.commit()

    async def fake_embed_queries(queries, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in queries]

    monkeypatch.setattr("backend.search.service.async_embed_queries", fake_embed_queries)

    ner_completed = {"value": False}

    def long_running_ner(query, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        # Should be cancelled before this returns. Sleep long enough that
        # if cleanup is broken, the test would take 5s instead of fast.
        import time as _time

        _time.sleep(5.0)
        ner_completed["value"] = True
        return ["should_not_arrive"]

    monkeypatch.setattr(
        "backend.search.service.extract_entities_from_query", long_running_ner
    )

    # Force vector candidates to be empty by blanking the candidate set.
    async def empty_candidate_set(*args, **kwargs):
        return type(
            "EmptySet",
            (),
            {"candidates": [], "call_count": 1, "duration_ms": 0.0},
        )()

    monkeypatch.setattr(
        "backend.search.service._async_build_vector_candidate_set",
        empty_candidate_set,
    )

    import time as _time

    started = _time.perf_counter()
    await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="anything",
        top_k=3,
        db=async_search_session,
        api_key="sk-test",
    )
    elapsed = _time.perf_counter() - started

    # Should return fast — the long-running NER must be abandoned.
    assert elapsed < 1.0, (
        f"Empty-vector early-return took {elapsed:.3f}s — NER cleanup "
        f"likely missing, the 5s sleep is being awaited."
    )
