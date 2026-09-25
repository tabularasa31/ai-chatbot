"""Tests for the hybrid retrieval pipeline (``search_similar_chunks_detailed_async``):
trace/observability contract, SQLite hybrid ranking behaviour, and NER-concurrency.

The POST /search endpoint was removed (dashboard/widget never called it); these
tests exercise the search service directly instead of going through the app.
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from backend.search.embedding import async_embed_queries, async_embed_queries_with_stats
from backend.search.query_variants import _async_rewrite_query_for_retrieval


@pytest.mark.asyncio
async def test_search_trace_pgvector_empty_path_records_vector_span(monkeypatch) -> None:
    from backend.search.pipeline import search_similar_chunks_detailed_async

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

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)
    async def fake_pgvector_search(*args, **kwargs):
        return []

    monkeypatch.setattr("backend.search.retrieval_db._async_pgvector_search", fake_pgvector_search)

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
    from backend.search.pipeline import search_similar_chunks_detailed_async

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

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)
    async def fake_pgvector_search(*args, **kwargs):
        return [(embedding, 0.91)]

    monkeypatch.setattr("backend.search.retrieval_db._async_pgvector_search", fake_pgvector_search)

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


@pytest.mark.asyncio
async def test_search_trace_sqlite_runs_full_stage_contract(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    """The SQLite retrieval pipeline runs every retrieval stage span, in order, with the
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

    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="sqlite_trace@example.com")
    tenant_id = _create_client(db_session, user, name="SQLite Trace Tenant").id

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
    from backend.search.pipeline import search_similar_chunks_detailed_async

    await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="Reset-password!!   reset password",
        top_k=2,
        db=async_search_session,
        api_key="sk-test",
        trace=fake_trace,
    )

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


@pytest.mark.asyncio
async def test_search_sqlite_deduplicates_variant_candidates_by_max_similarity(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    """A chunk matched by more than one query variant keeps its best (max) vector
    similarity, and appears only once in the final results — not once per variant.

    ``shared`` scores weakly against the first query variant but strongly against
    the second; ``tertiary`` scores in between and is only matched by the third
    variant. If dedup kept a variant's first-seen score instead of the max,
    ``tertiary`` would incorrectly outrank ``shared`` in the final response.
    """
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="variant_dedup@example.com")
    tenant_id = _create_client(db_session, user, name="Variant Dedup Tenant").id

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

    from backend.search.pipeline import search_similar_chunks_detailed_async

    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="Widget-setup!!   widget setup",
        top_k=2,
        db=async_search_session,
        api_key="sk-test",
    )
    data = [emb for emb, _similarity in bundle.results]

    result_texts = [item.chunk_text for item in data]
    assert len(result_texts) == len(set(result_texts))
    assert result_texts.index("alpha bravo charlie") < result_texts.index("delta echo foxtrot")


@pytest.mark.asyncio
async def test_search_trace_uses_script_bucket_naming_for_script_boost_and_mmr(
    monkeypatch,
) -> None:
    from backend.models import Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async

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

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)
    async def fake_pgvector_search(*args, **kwargs):
        return [
            (cyrillic_primary, 0.95),
            (cyrillic_duplicate, 0.92),
            (latin_diverse, 0.7),
        ]

    monkeypatch.setattr("backend.search.retrieval_db._async_pgvector_search", fake_pgvector_search)
    # FakeDB doesn't implement .execute(); pretend the tenant has embeddings
    # so the entity-overlap channel proceeds in this trace-contract test.
    async def fake_tenant_has_embeddings(*args, **kwargs):
        return True

    monkeypatch.setattr(
        "backend.search.pipeline._async_tenant_has_embeddings",
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
    from backend.search.embedding import async_embed_query

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


@pytest.mark.asyncio
async def test_search_single_embedding_match(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    """Create tenant, document, embedding; mock the embedding call to return a similar vector."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    vec = [0.1] * 1536
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=vec)]

    user = _create_user(db_session, email="single@example.com")
    tenant_id = _create_client(db_session, user, name="Single Tenant").id

    doc = Document(
        tenant_id=tenant_id,
        filename="doc.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Relevant content here.",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="Relevant content here.",
            vector=None,
            metadata_json={"chunk_index": 0, "vector": vec},
        )
    )
    db_session.commit()

    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="relevant content",
        top_k=3,
        db=async_search_session,
        api_key="sk-test",
    )
    results = bundle.results
    assert len(results) == 1
    assert results[0][0].document_id == doc.id
    assert results[0][1] > 0.0
    assert "Relevant content" in results[0][0].chunk_text


@pytest.mark.asyncio
async def test_search_sorts_by_similarity_desc_and_respects_top_k(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    """Journey over one embedding set, guarding two failure modes:
    - results are sorted DESC by similarity
    - top_k truncates the result count even when more embeddings exist
    """
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="multi@example.com")
    tenant_id = _create_client(db_session, user, name="Multi Tenant").id

    doc = Document(
        tenant_id=tenant_id,
        filename="multi.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="chunk0 chunk1 chunk2 chunk3 chunk4",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    # Vectors in different directions for distinct similarity scores
    # query: [1,0,0,...]; each successive vector is further from it.
    query_vec = [1.0] + [0.0] * 1535
    vectors = [
        [0.99, 0.1] + [0.0] * 1534,
        [0.9, 0.3] + [0.0] * 1534,
        [0.5, 0.5] + [0.0] * 1534,
        [0.1, 0.9] + [0.0] * 1534,
        [0.0, 1.0] + [0.0] * 1534,
    ]
    for i, v in enumerate(vectors):
        emb = Embedding(
            document_id=doc.id,
            chunk_text=f"chunk{i}",
            vector=None,
            metadata_json={"chunk_index": i, "vector": v},
        )
        db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    all_bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="search",
        top_k=5,
        db=async_search_session,
        api_key="sk-test",
    )
    all_results = all_bundle.results
    assert len(all_results) == 5
    sims = [similarity for _emb, similarity in all_results]
    assert sims == sorted(sims, reverse=True)

    truncated_bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="search",
        top_k=2,
        db=async_search_session,
        api_key="sk-test",
    )
    truncated_results = truncated_bundle.results
    assert len(truncated_results) == 2
    assert [emb.chunk_text for emb, _similarity in truncated_results] == [
        emb.chunk_text for emb, _similarity in all_results[:2]
    ]


@pytest.mark.asyncio
async def test_search_other_client_isolated(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    """Create embeddings for tenant A and B; search as user A → only A's results."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user_a = _create_user(db_session, email="isol_a@example.com")
    client_a_id = _create_client(db_session, user_a, name="Tenant A").id

    user_b = _create_user(db_session, email="isol_b@example.com")
    client_b_id = _create_client(db_session, user_b, name="Tenant B").id

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

    bundle = await search_similar_chunks_detailed_async(
        tenant_id=client_a_id,
        query="secret",
        top_k=5,
        db=async_search_session,
        api_key="sk-test",
    )
    results = bundle.results
    assert len(results) == 1
    assert results[0][0].document_id == doc_a.id
    assert "Tenant A" in results[0][0].chunk_text


@pytest.mark.asyncio
async def test_search_no_embeddings_returns_empty_results(
    mock_openai_client: Mock, db_session: Session, async_search_session
) -> None:
    """No embeddings in DB → empty results."""
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="default@example.com")
    tenant_id = _create_client(db_session, user, name="Default Tenant").id
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="anything",
        top_k=3,
        db=async_search_session,
        api_key="sk-test",
    )
    assert bundle.results == []


# --- BM25 search unit tests ---


@pytest.mark.rag_edge
@pytest.mark.asyncio
async def test_search_low_vector_similarity_still_returns_chunk(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    """SQLite shared pipeline still returns lexical matches even with zero vector confidence."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="fallback@example.com")
    tenant_id = _create_client(db_session, user, name="Fallback Tenant").id

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

    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="cors",
        top_k=3,
        db=async_search_session,
        api_key="sk-test",
    )
    results = bundle.results
    assert len(results) == 1
    assert "CORS" in results[0][0].chunk_text
    assert results[0][0].document_id == doc.id
    assert results[0][1] > 0.0


@pytest.mark.asyncio
async def test_search_sqlite_hybrid_pipeline_allows_lexical_signal_to_outrank_purer_cosine(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="sqlitehybrid@example.com")
    tenant_id = _create_client(db_session, user, name="SQLite Hybrid Tenant").id

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

    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="cors configuration",
        top_k=2,
        db=async_search_session,
        api_key="sk-test",
    )

    results = bundle.results
    assert len(results) == 2
    assert "cors configuration settings" in [emb.chunk_text for emb, _sim in results]
    assert results[0][0].chunk_text == "cors configuration settings"


@pytest.mark.rag_edge
@pytest.mark.parametrize(
    "bad_vectors",
    [
        pytest.param(["not-a-list", []], id="malformed_metadata_vectors_string_and_empty"),
        pytest.param([[0.1] * 10], id="vector_with_wrong_dimension"),
    ],
)
@pytest.mark.asyncio
async def test_search_skips_unusable_vectors(
    mock_openai_client: Mock,
    db_session: Session,
    async_search_session,
    bad_vectors: list,
) -> None:
    """A malformed or wrong-dimension vector in metadata_json must be skipped, not crash the search."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="badvec@example.com")
    tenant_id = _create_client(db_session, user, name="Bad Vec Tenant").id

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
                chunk_text=f"bad vector chunk {i}",
                vector=None,
                metadata_json={"chunk_index": i, "vector": v},
            )
            for i, v in enumerate(bad_vectors)
        ]
    )
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant_id,
        query="content",
        top_k=5,
        db=async_search_session,
        api_key="sk-test",
    )
    assert bundle.results == []


# --- Unit tests for cross-lingual query expansion ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query, llm_content, side_effect, expected",
    [
        pytest.param(
            "Почему бот не отвечает на русском?",
            "определение языка мультиязычная поддержка",
            None,
            "определение языка мультиязычная поддержка",
            id="happy_path_returns_rewritten_query",
        ),
        pytest.param(
            "Почему бот не отвечает на русском?",
            None,
            RuntimeError("timeout"),
            None,
            id="llm_failure_degrades_to_none",
        ),
        pytest.param(
            "",
            "",
            None,
            None,
            id="empty_llm_response_does_not_yield_blank_variant",
        ),
    ],
)
async def test_rewrite_query_for_retrieval(
    query: str, llm_content: str | None, side_effect: Exception | None, expected: str | None
) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    if side_effect is not None:
        call_mock = AsyncMock(side_effect=side_effect)
    else:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = llm_content
        call_mock = AsyncMock(return_value=mock_response)

    with patch("backend.search.query_variants.get_async_openai_client") as mock_client_factory, patch(
        "backend.search.query_variants.async_call_openai_with_retry",
        new=call_mock,
    ):
        mock_client_factory.return_value = MagicMock()
        result = await _async_rewrite_query_for_retrieval(query, api_key="test-key")

    assert result == expected


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
    from backend.search.pipeline import search_similar_chunks_detailed_async
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

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)

    # Make the vector candidate build "slow" so NER has time to run in parallel.
    real_build = __import__(
        "backend.search.retrieval_db", fromlist=["_async_build_vector_candidate_set"]
    )._async_build_vector_candidate_set

    async def slow_vector_build(*args, **kwargs):
        await asyncio.sleep(0.3)
        return await real_build(*args, **kwargs)

    monkeypatch.setattr(
        "backend.search.pipeline._async_build_vector_candidate_set", slow_vector_build
    )

    def slow_ner(query, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        _time.sleep(0.3)
        return ["hello"]

    monkeypatch.setattr(
        "backend.search.pipeline.extract_entities_from_query", slow_ner
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
    from backend.search.pipeline import search_similar_chunks_detailed_async
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="no_emb@example.com")
    tenant_id = _create_client(db_session, user, name="No Embeddings").id
    # No documents, no embeddings — tenant exists but has nothing indexed.

    async def fake_embed_queries(queries, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in queries]

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)

    ner_calls: list[str] = []

    def tracking_ner(query, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        ner_calls.append(query)
        return ["should_not_be_called"]

    monkeypatch.setattr(
        "backend.search.pipeline.extract_entities_from_query", tracking_ner
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
    from backend.search.pipeline import search_similar_chunks_detailed_async
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

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)

    ner_completed = {"value": False}

    def long_running_ner(query, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        # Should be cancelled before this returns. Sleep long enough that
        # if cleanup is broken, the test would take 5s instead of fast.
        import time as _time

        _time.sleep(5.0)
        ner_completed["value"] = True
        return ["should_not_arrive"]

    monkeypatch.setattr(
        "backend.search.pipeline.extract_entities_from_query", long_running_ner
    )

    # Force vector candidates to be empty by blanking the candidate set.
    async def empty_candidate_set(*args, **kwargs):
        return type(
            "EmptySet",
            (),
            {"candidates": [], "call_count": 1, "duration_ms": 0.0},
        )()

    monkeypatch.setattr(
        "backend.search.pipeline._async_build_vector_candidate_set",
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
