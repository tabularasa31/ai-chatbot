"""Tests for vector search API."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.search.service import (
    ContradictionPair,
    SourceOverlapPair,
    apply_script_boost,
    bm25_search_chunks,
    build_reliability_assessment,
    build_reliability_projection,
    cosine_similarity,
    detect_metadata_contradictions,
    embed_queries,
    embed_queries_with_stats,
    detect_query_script_bucket,
    detect_source_overlaps,
    expand_query,
    mmr_select,
    rerank_candidates,
    serialize_reliability,
)


# --- Unit tests for cosine_similarity ---


def test_cosine_similarity_basic() -> None:
    """Identical vectors → 1.0, orthogonal → ~0."""
    vec = [1.0, 0.0, 0.0]
    assert cosine_similarity(vec, vec) == 1.0

    orth_a = [1.0, 0.0, 0.0]
    orth_b = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(orth_a, orth_b)) < 0.001

    # Same direction, different magnitude
    a = [2.0, 0.0, 0.0]
    b = [3.0, 0.0, 0.0]
    assert abs(cosine_similarity(a, b) - 1.0) < 0.001


def test_cosine_similarity_zero_vectors() -> None:
    """Zero vectors → 0.0 (safe handling)."""
    zero = [0.0, 0.0, 0.0]
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(zero, vec) == 0.0
    assert cosine_similarity(vec, zero) == 0.0
    assert cosine_similarity(zero, zero) == 0.0


def test_search_trace_pgvector_empty_path_records_vector_span(monkeypatch) -> None:
    from backend.search.service import search_similar_chunks_detailed

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

    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [[0.1] * 3 for _ in queries],
    )
    monkeypatch.setattr("backend.search.service._pgvector_search", lambda *args, **kwargs: [])

    trace = FakeTrace()
    bundle = search_similar_chunks_detailed(
        client_id=uuid.uuid4(),
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


def test_search_trace_multi_variant_pgvector_reports_extra_work(monkeypatch) -> None:
    from backend.models import Embedding
    from backend.search.service import search_similar_chunks_detailed

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

    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [[0.1] * 3 for _ in queries],
    )
    monkeypatch.setattr(
        "backend.search.service._pgvector_search",
        lambda *args, **kwargs: [(embedding, 0.91)],
    )

    trace = FakeTrace()
    bundle = search_similar_chunks_detailed(
        client_id=uuid.uuid4(),
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


def test_search_trace_sqlite_runs_full_stage_contract(monkeypatch, db_session: Session) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding
    from backend.search.service import search_similar_chunks_detailed
    from tests.test_models import _create_client, _create_user

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

    user = _create_user(db_session, email="sqlite_trace@example.com")
    client_id = _create_client(db_session, user, name="SQLite Trace").id
    doc = Document(
        client_id=client_id,
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

    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [[1.0, 0.0, 0.0] for _ in queries],
    )

    trace = FakeTrace()
    bundle = search_similar_chunks_detailed(
        client_id=client_id,
        query="Reset-password!!   reset password",
        top_k=2,
        db=db_session,
        api_key="sk-test",
        trace=trace,
    )

    assert bundle.query_variant_count == 3
    assert bundle.vector_search_call_count == 3
    assert bundle.extra_vector_search_calls == 2
    assert bundle.has_lexical_signal is True
    assert [span.name for span in trace.spans] == [
        "query-expansion",
        "query-embedding",
        "vector-search",
        "bm25-search",
        "rrf-fusion",
        "reranking",
        "script-boost",
        "mmr-pass",
        "source-overlap-check",
    ]

    vector_span = next(span for span in trace.spans if span.name == "vector-search")
    bm25_span = next(span for span in trace.spans if span.name == "bm25-search")
    overlap_span = next(span for span in trace.spans if span.name == "source-overlap-check")
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


def test_search_sqlite_observability_counts_executed_variants(monkeypatch) -> None:
    from backend.models import Embedding
    from backend.search.service import search_similar_chunks_detailed

    class FakeBind:
        url = "sqlite://test"

    class FakeDB:
        bind = FakeBind()

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password instructions",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [[float(index)] for index, _ in enumerate(queries, start=1)],
    )
    monkeypatch.setattr(
        "backend.search.service._python_cosine_search",
        lambda *args, **kwargs: [(embedding, 0.91)],
    )

    bundle = search_similar_chunks_detailed(
        client_id=uuid.uuid4(),
        query="Reset-password!!   reset password",
        top_k=2,
        db=FakeDB(),
        api_key="sk-test",
    )

    assert bundle.query_variant_count == 3
    assert bundle.vector_search_call_count == 3
    assert bundle.extra_vector_search_calls == 2


def test_search_sqlite_deduplicates_variant_candidates_by_max_similarity(monkeypatch) -> None:
    from backend.models import Embedding
    from backend.search.service import BM25SearchBundle, BM25Winner, search_similar_chunks_detailed

    class FakeBind:
        url = "sqlite://test"

    class FakeDB:
        bind = FakeBind()

    shared = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password instructions",
        metadata_json={"chunk_index": 0},
    )
    secondary = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="download invoice guide",
        metadata_json={"chunk_index": 1},
    )
    tertiary = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="rotate api key settings",
        metadata_json={"chunk_index": 2},
    )

    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [[float(index)] for index, _ in enumerate(queries, start=1)],
    )

    def fake_python_cosine_search(
        client_id: uuid.UUID,
        query_vector: list[float],
        top_k: int,
        db,
    ) -> list[tuple[Embedding, float]]:
        marker = int(query_vector[0])
        if marker == 1:
            return [(shared, 0.4), (secondary, 0.3)]
        if marker == 2:
            return [(shared, 0.9)]
        return [(tertiary, 0.2)]

    captured: dict[str, object] = {}

    def fake_run_bm25_search(
        candidates: list[Embedding],
        *,
        query: str,
        variant_queries: list[str],
        top_k: int,
        expansion_mode: str,
    ) -> BM25SearchBundle:
        captured["candidate_ids"] = [embedding.id for embedding in candidates]
        return BM25SearchBundle(
            results=[(secondary, 1.0)],
            has_lexical_signal=True,
            variant_queries=[query],
            variant_eval_count=1,
            merged_hit_count_before_cap=1,
            merged_hit_count_after_cap=1,
            winner_by_id={
                secondary.id: BM25Winner(
                    variant_index=0,
                    variant_query=query,
                    score=1.0,
                )
            },
        )

    monkeypatch.setattr(
        "backend.search.service._python_cosine_search",
        fake_python_cosine_search,
    )
    monkeypatch.setattr(
        "backend.search.service._run_bm25_search",
        fake_run_bm25_search,
    )

    bundle = search_similar_chunks_detailed(
        client_id=uuid.uuid4(),
        query="Reset-password!!   reset password",
        top_k=2,
        db=FakeDB(),
        api_key="sk-test",
    )

    candidate_ids = captured["candidate_ids"]
    assert candidate_ids == [shared.id, secondary.id, tertiary.id]
    assert bundle.best_vector_similarity == 0.9
    assert len({embedding.id for embedding, _ in bundle.results}) == len(bundle.results)


def test_lexical_safe_query_variants_dedupes_after_normalization() -> None:
    from backend.search.service import lexical_safe_query_variants

    variants = lexical_safe_query_variants(
        "Reset password",
        base_variants=[
            " Reset   password ",
            "reset password",
            "RESET PASSWORD",
            "Reset password",
        ],
    )

    assert variants == ["Reset password"]


def test_run_bm25_search_symmetric_merge_deduplicates_hits_and_keeps_earliest_tie_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    first = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="reset password guide")
    second = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="password reset checklist")
    third = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="account recovery flow")

    def fake_score(prepared_corpus, query: str, top_k: int):
        if query == "reset password":
            return [(first, 1.0), (second, 0.6)]
        return [(first, 1.0), (third, 0.9)]

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    bundle = _run_bm25_search(
        [first, second, third],
        query="reset password",
        variant_queries=["reset password", "password reset"],
        top_k=5,
        expansion_mode="symmetric_variants",
    )

    assert [embedding.id for embedding, _ in bundle.results] == [first.id, third.id, second.id]
    assert bundle.merged_hit_count_before_cap == 3
    assert bundle.merged_hit_count_after_cap == 3
    assert bundle.winner_by_id[first.id].variant_index == 0


def test_run_bm25_search_symmetric_mode_can_match_asymmetric_when_no_effective_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    first = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="cors settings")
    second = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="api key rotation")

    def fake_score(prepared_corpus, query: str, top_k: int):
        return [(first, 1.0), (second, 0.5)]

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    asymmetric = _run_bm25_search(
        [first, second],
        query="cors settings",
        variant_queries=["cors settings"],
        top_k=5,
        expansion_mode="asymmetric",
    )
    symmetric = _run_bm25_search(
        [first, second],
        query="cors settings",
        variant_queries=["cors settings", "cors config"],
        top_k=5,
        expansion_mode="symmetric_variants",
    )

    assert symmetric.results == asymmetric.results
    assert symmetric.winner_by_id[first.id].variant_index == 0
    assert symmetric.merged_hit_count_before_cap == asymmetric.merged_hit_count_before_cap


def test_run_bm25_search_applies_cap_after_deterministic_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    first = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="reset password guide")
    second = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="password reset checklist")
    third = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="account recovery")

    def fake_score(prepared_corpus, query: str, top_k: int):
        if query == "reset password":
            return [(first, 1.0)]
        if query == "password reset":
            return [(second, 0.9)]
        return [(third, 0.8)]

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    bundle = _run_bm25_search(
        [first, second, third],
        query="reset password",
        variant_queries=["reset password", "password reset", "account recovery"],
        top_k=2,
        expansion_mode="symmetric_variants",
    )

    assert bundle.merged_hit_count_before_cap == 3
    assert bundle.merged_hit_count_after_cap == 2
    assert [embedding.id for embedding, _ in bundle.results] == [first.id, second.id]


def test_run_bm25_search_uses_final_merged_output_for_lexical_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    alias_hit = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="alias documentation")

    def fake_score(prepared_corpus, query: str, top_k: int):
        if query == "alias":
            return [(alias_hit, 1.0)]
        return []

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    bundle = _run_bm25_search(
        [alias_hit],
        query="primary",
        variant_queries=["primary", "alias"],
        top_k=5,
        expansion_mode="symmetric_variants",
    )

    assert bundle.results == [(alias_hit, 1.0)]
    assert bundle.has_lexical_signal is False


def test_search_trace_uses_script_bucket_naming_for_script_boost_and_mmr(
    monkeypatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import search_similar_chunks_detailed

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

    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [[0.1] * 3 for _ in queries],
    )
    monkeypatch.setattr(
        "backend.search.service._pgvector_search",
        lambda *args, **kwargs: [
            (cyrillic_primary, 0.95),
            (cyrillic_duplicate, 0.92),
            (latin_diverse, 0.7),
        ],
    )

    trace = FakeTrace()
    bundle = search_similar_chunks_detailed(
        client_id=uuid.uuid4(),
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


def test_embed_query_uses_openai_client(mock_openai_client: Mock) -> None:
    """embed_query calls OpenAI with correct model name."""
    from backend.search.service import embed_query

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    embed_query("test query", api_key="sk-test")
    mock_openai_client.embeddings.create.assert_called_once()
    call_kwargs = mock_openai_client.embeddings.create.call_args
    assert call_kwargs.kwargs.get("model") == "text-embedding-3-small"
    assert call_kwargs.kwargs.get("input") == "test query"


def test_embed_queries_batches_variants_into_single_openai_call(mock_openai_client: Mock) -> None:
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 3),
        Mock(embedding=[0.2] * 3),
    ]

    vectors = embed_queries(["first", "second"], api_key="sk-test")

    assert vectors == [[0.1] * 3, [0.2] * 3]
    mock_openai_client.embeddings.create.assert_called_once()
    call_kwargs = mock_openai_client.embeddings.create.call_args
    assert call_kwargs.kwargs.get("input") == ["first", "second"]


def test_embed_queries_with_stats_reports_actual_request_count(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 3),
        Mock(embedding=[0.2] * 3),
    ]

    vectors, request_count = embed_queries_with_stats(
        ["first", "second"],
        api_key="sk-test",
    )

    assert vectors == [[0.1] * 3, [0.2] * 3]
    assert request_count == 1
    mock_openai_client.embeddings.create.assert_called_once()


def test_expand_query_deduplicates_and_normalizes() -> None:
    variants = expand_query("Reset-password!!   reset password")
    assert variants == [
        "Reset-password!! reset password",
        "Reset password reset password",
        "reset password",
    ]


def test_expand_query_preserves_empty_query_as_single_variant() -> None:
    assert expand_query("") == [""]


def test_rerank_candidates_boosts_lexical_match() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="how to reset your password in the dashboard",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="billing invoice download instructions",
        metadata_json={"chunk_index": 1},
    )

    reranked = rerank_candidates(
        "reset password",
        [(second, 0.9), (first, 0.7)],
        vector_scores={first.id: 0.7, second.id: 0.9},
        bm25_scores={first.id: 1.0, second.id: 0.1},
        top_k=2,
    )

    assert [item[0].id for item in reranked] == [first.id, second.id]
    assert reranked[0][1] > reranked[1][1]


def test_detect_query_script_bucket_distinguishes_cyrillic() -> None:
    assert detect_query_script_bucket("как сбросить пароль") == "cyrillic"
    assert detect_query_script_bucket("reset password") == "latin"


def test_detect_query_script_bucket_uses_other_for_non_latin_non_cyrillic() -> None:
    assert detect_query_script_bucket("パスワードをリセット") == "other"


def test_detect_query_script_bucket_prefers_cyrillic_for_mixed_script_query() -> None:
    assert detect_query_script_bucket("OpenAI для русского") == "cyrillic"


def test_apply_script_boost_prefers_matching_script_bucket() -> None:
    from backend.models import Embedding

    english = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings",
        metadata_json={"language": "en"},
    )
    russian = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="сброс пароля в настройках",
        metadata_json={"language": "ru"},
    )

    boosted = apply_script_boost(
        "cyrillic",
        [(english, 0.81), (russian, 0.79)],
        top_k=2,
    )

    assert [item[0].id for item in boosted] == [russian.id, english.id]


def test_apply_script_boost_treats_ukrainian_metadata_as_cyrillic() -> None:
    from backend.models import Embedding

    english = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings",
        metadata_json={"language": "en"},
    )
    ukrainian = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="скинути пароль в налаштуваннях",
        metadata_json={"language": "uk"},
    )

    boosted = apply_script_boost(
        "cyrillic",
        [(english, 0.81), (ukrainian, 0.79)],
        top_k=2,
    )

    assert [item[0].id for item in boosted] == [ukrainian.id, english.id]


def test_mmr_select_replaces_near_duplicate_chunk() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )
    duplicate = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1},
    )
    diverse = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="download billing invoice from account page",
        metadata_json={"chunk_index": 2},
    )

    selection = mmr_select(
        [(first, 0.95), (duplicate, 0.92), (diverse, 0.7)],
        top_k=2,
    )
    selected = selection.results
    replacements = selection.replacements

    assert [item[0].id for item in selected] == [first.id, diverse.id]
    assert selected[0][1] == 0.95
    assert selected[1][1] == 0.7
    assert replacements == [
        {
            "removed_chunk_id": str(duplicate.id),
            "replacement_chunk_id": str(diverse.id),
            "reason": "removed_baseline_redundancy:0.833",
            "removed_redundancy": 0.833333,
            "replacement_redundancy": 0.0,
        }
    ]
    assert selection.diagnostics == [
        {
            "selected_chunk_id": str(first.id),
            "selected_rank": 1,
            "base_score": 0.95,
            "mmr_score": 0.95,
            "redundancy_penalty": 0.0,
        },
        {
            "selected_chunk_id": str(diverse.id),
            "selected_rank": 2,
            "base_score": 0.7,
            "mmr_score": 0.49,
            "redundancy_penalty": 0.0,
        },
    ]


def test_mmr_select_handles_empty_candidates() -> None:
    selection = mmr_select([], top_k=3)

    assert selection.results == []
    assert selection.replacements == []
    assert selection.diagnostics == []


def test_mmr_select_returns_available_candidates_when_fewer_than_top_k(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from backend.models import Embedding

    only = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="single chunk",
        metadata_json={"chunk_index": 0},
    )

    selection = mmr_select([(only, 0.88)], top_k=3)

    assert selection.results == [(only, 0.88)]
    assert any("fewer candidates than requested top_k" in message for message in caplog.messages)


def test_rerank_candidates_uses_widened_bm25_scores_without_zeroing_tail_candidates() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password steps",
        metadata_json={"chunk_index": 1},
    )
    third = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="password reset troubleshooting",
        metadata_json={"chunk_index": 2},
    )

    reranked = rerank_candidates(
        "reset password",
        [(first, 0.9), (second, 0.8), (third, 0.7)],
        vector_scores={first.id: 0.9, second.id: 0.8, third.id: 0.7},
        bm25_scores={first.id: 1.0, second.id: 0.8, third.id: 0.6},
        top_k=3,
    )

    assert len(reranked) == 3
    assert reranked[2][1] > 0.0


def test_rerank_candidates_uses_widened_bm25_scores_without_zeroing_tail_candidates() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password steps",
        metadata_json={"chunk_index": 1},
    )
    third = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="password reset troubleshooting",
        metadata_json={"chunk_index": 2},
    )

    reranked = rerank_candidates(
        "reset password",
        [(first, 0.9), (second, 0.8), (third, 0.7)],
        vector_scores={first.id: 0.9, second.id: 0.8, third.id: 0.7},
        bm25_scores={first.id: 1.0, second.id: 0.8, third.id: 0.6},
        top_k=3,
    )

    assert len(reranked) == 3
    assert reranked[2][1] > 0.0


def test_detect_source_overlaps_flags_duplicate_chunks_from_different_docs() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1},
    )

    source_overlap_detected, source_overlap_pairs = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    )

    assert source_overlap_detected is True
    assert source_overlap_pairs == (
        SourceOverlapPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            similarity=0.8333,
        ),
    )


def test_build_reliability_assessment_uses_overlap_signal_without_conflict_semantics() -> None:
    overlap_pair = SourceOverlapPair(
        chunk_a_id="a",
        chunk_b_id="b",
        similarity=0.88,
    )

    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(overlap_pair,),
        source_overlap_similarity_threshold=0.75,
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "medium",
        "cap": "medium",
        "cap_reason": "source_overlap",
        "signals": [{"kind": "source_overlap"}],
        "evidence": {
            "source_overlap": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "similarity": 0.88,
                        "signal_type": "cross_document_overlap",
                    }
                ],
                "similarity_threshold": 0.75,
            }
        },
    }

    projection = build_reliability_projection(reliability)
    assert projection["source_overlap_detected"] is True
    assert projection["source_overlap_pairs"] == [
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "similarity": 0.88,
            "signal_type": "cross_document_overlap",
        }
    ]
    assert projection["contradiction_detected"] is False
    assert projection["contradiction_count"] == 0
    assert projection["contradiction_pair_count"] == 0
    assert projection["contradiction_basis_types"] == []
    assert projection["reliability"]["score"] == "medium"
    assert projection["reliability"]["cap_reason"] == "source_overlap"


def test_build_reliability_assessment_no_signal_serializes_stable_empty_shape() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "high",
        "cap": None,
        "cap_reason": None,
        "signals": [],
        "evidence": {},
    }

    projection = build_reliability_projection(reliability)
    assert projection["source_overlap_detected"] is False
    assert projection["source_overlap_pairs"] == []
    assert projection["contradiction_detected"] is False
    assert projection["contradiction_count"] == 0
    assert projection["contradiction_pair_count"] == 0
    assert projection["contradiction_basis_types"] == []
    assert projection["reliability"]["score"] == "high"
    assert projection["reliability"]["cap_reason"] is None


def test_build_reliability_assessment_overlap_cap_is_not_applied_when_base_score_is_already_medium() -> None:
    reliability = build_reliability_assessment(
        top_score=0.6,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
    )

    assert reliability.base_score == "medium"
    assert reliability.score == "medium"
    assert reliability.cap is None
    assert reliability.cap_reason is None
    assert serialize_reliability(reliability)["signals"] == [{"kind": "source_overlap"}]


def test_detect_metadata_contradictions_flags_effective_date_disagreement_on_overlap_pairs() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0, "effective_date": "2024-03-01"},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1, "effective_date": "2025-03-01"},
    )

    overlap_pairs = (
        SourceOverlapPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            similarity=0.83,
        ),
    )

    contradiction_pairs = detect_metadata_contradictions(
        [(first, 0.9), (second, 0.88)],
        overlap_pairs,
    )

    assert contradiction_pairs == (
        ContradictionPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            basis="effective_date",
            value_a="2024-03-01",
            value_b="2025-03-01",
        ),
    )


def test_detect_metadata_contradictions_ignores_single_sided_metadata() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0, "effective_date": "2024-03-01"},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1},
    )

    contradiction_pairs = detect_metadata_contradictions(
        [(first, 0.9), (second, 0.88)],
        (
            SourceOverlapPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                similarity=0.83,
            ),
        ),
    )

    assert contradiction_pairs == ()


def test_detect_metadata_contradictions_treats_date_granularity_as_compatible() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0, "effective_date": "2024"},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1, "effective_date": "2024-03"},
    )

    contradiction_pairs = detect_metadata_contradictions(
        [(first, 0.9), (second, 0.88)],
        (
            SourceOverlapPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                similarity=0.83,
            ),
        ),
    )

    assert contradiction_pairs == ()


def test_detect_metadata_contradictions_normalizes_version_equivalence() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0, "version": "v2"},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1, "version": "2.0"},
    )

    contradiction_pairs = detect_metadata_contradictions(
        [(first, 0.9), (second, 0.88)],
        (
            SourceOverlapPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                similarity=0.83,
            ),
        ),
    )

    assert contradiction_pairs == ()


def test_detect_metadata_contradictions_can_emit_multiple_facts_for_one_overlap_pair() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={
            "chunk_index": 0,
            "effective_date": "2024-03-01",
            "version": "v2",
        },
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={
            "chunk_index": 1,
            "effective_date": "2025-03-01",
            "version": "v3",
        },
    )

    contradiction_pairs = detect_metadata_contradictions(
        [(first, 0.9), (second, 0.88)],
        (
            SourceOverlapPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                similarity=0.83,
            ),
        ),
    )

    assert contradiction_pairs == (
        ContradictionPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            basis="effective_date",
            value_a="2024-03-01",
            value_b="2025-03-01",
        ),
        ContradictionPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            basis="version",
            value_a="v2",
            value_b="v3",
        ),
    )


def test_build_reliability_assessment_keeps_single_contradiction_as_evidence_only() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "medium",
        "cap": "medium",
        "cap_reason": "source_overlap",
        "signals": [{"kind": "source_overlap"}, {"kind": "contradiction"}],
        "evidence": {
            "source_overlap": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "similarity": 0.81,
                        "signal_type": "cross_document_overlap",
                    }
                ],
                "similarity_threshold": 0.75,
            },
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    }
                ]
            },
        },
    }


def test_build_reliability_assessment_caps_to_low_for_multiple_facts_on_same_pair() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="version",
                value_a="v2",
                value_b="v3",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "low",
        "cap": "low",
        "cap_reason": "contradiction",
        "signals": [{"kind": "source_overlap"}, {"kind": "contradiction"}],
        "evidence": {
            "source_overlap": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "similarity": 0.81,
                        "signal_type": "cross_document_overlap",
                    }
                ],
                "similarity_threshold": 0.75,
            },
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    },
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "version",
                        "value_a": "v2",
                        "value_b": "v3",
                    },
                ]
            },
        },
    }


def test_build_reliability_assessment_caps_to_low_for_multiple_distinct_same_basis_facts() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="revision",
                value_a="rev 1",
                value_b="rev 2",
            ),
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="revision",
                value_a="rev 3",
                value_b="rev 4",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "low",
        "cap": "low",
        "cap_reason": "contradiction",
        "signals": [{"kind": "contradiction"}],
        "evidence": {
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "revision",
                        "value_a": "rev 1",
                        "value_b": "rev 2",
                    },
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "revision",
                        "value_a": "rev 3",
                        "value_b": "rev 4",
                    },
                ]
            },
        },
    }


def test_build_reliability_assessment_caps_to_low_for_contradictions_across_pairs() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="c",
                chunk_b_id="d",
                basis="version",
                value_a="v2",
                value_b="v3",
            ),
        ),
    )

    assert reliability.score == "low"
    assert reliability.cap == "low"
    assert reliability.cap_reason == "contradiction"
    assert serialize_reliability(reliability)["signals"] == [{"kind": "contradiction"}]


def test_build_reliability_assessment_filters_invalid_contradictions_before_threshold() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="c",
                chunk_b_id="d",
                basis="",
                value_a="v2",
                value_b="v3",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "high",
        "cap": None,
        "cap_reason": None,
        "signals": [{"kind": "contradiction"}],
        "evidence": {
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    }
                ]
            },
        },
    }


def test_build_reliability_assessment_deduplicates_exact_duplicate_contradictions() -> None:
    contradiction = ContradictionPair(
        chunk_a_id="a",
        chunk_b_id="b",
        basis="effective_date",
        value_a="2024-03-01",
        value_b="2025-03-01",
    )
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(contradiction, contradiction),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "high",
        "cap": None,
        "cap_reason": None,
        "signals": [{"kind": "contradiction"}],
        "evidence": {
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    }
                ]
            },
        },
    }


def test_build_reliability_assessment_deduplicates_mirrored_duplicate_contradictions() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="b",
                chunk_b_id="a",
                basis="effective_date",
                value_a="2025-03-01",
                value_b="2024-03-01",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "high",
        "cap": None,
        "cap_reason": None,
        "signals": [{"kind": "contradiction"}],
        "evidence": {
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    }
                ]
            },
        },
    }


def test_build_reliability_assessment_counts_mirrored_distinct_facts_on_one_logical_pair() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="b",
                chunk_b_id="a",
                basis="version",
                value_a="v3",
                value_b="v2",
            ),
        ),
    )

    assert reliability.score == "low"
    assert reliability.cap == "low"
    assert reliability.cap_reason == "contradiction"
    assert serialize_reliability(reliability)["evidence"]["contradiction"]["pairs"] == [
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "basis": "effective_date",
            "value_a": "2024-03-01",
            "value_b": "2025-03-01",
        },
        {
            "chunk_a_id": "b",
            "chunk_b_id": "a",
            "basis": "version",
            "value_a": "v3",
            "value_b": "v2",
        },
    ]


def test_build_reliability_assessment_contradiction_cap_short_circuits_overlap_cap() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="c",
                chunk_b_id="d",
                basis="version",
                value_a="v2",
                value_b="v3",
            ),
        ),
    )

    assert reliability.score == "low"
    assert reliability.cap == "low"
    assert reliability.cap_reason == "contradiction"


def test_build_reliability_assessment_keeps_contradiction_reason_when_base_score_is_low() -> None:
    reliability = build_reliability_assessment(
        top_score=0.4,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="c",
                chunk_b_id="d",
                basis="version",
                value_a="v2",
                value_b="v3",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "low",
        "score": "low",
        "cap": "low",
        "cap_reason": "contradiction",
        "signals": [{"kind": "low_top_score"}, {"kind": "contradiction"}],
        "evidence": {
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    },
                    {
                        "chunk_a_id": "c",
                        "chunk_b_id": "d",
                        "basis": "version",
                        "value_a": "v2",
                        "value_b": "v3",
                    },
                ]
            },
        },
    }


def test_build_reliability_projection_does_not_mutate_canonical_object() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
    )
    before = serialize_reliability(reliability)
    projection = build_reliability_projection(reliability)

    assert projection["reliability"] == before
    assert serialize_reliability(reliability) == before


def test_build_reliability_projection_derives_contradiction_metrics_from_final_canonical_entries() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="version",
                value_a="v2",
                value_b="v3",
            ),
            ContradictionPair(
                chunk_a_id="c",
                chunk_b_id="d",
                basis="effective_date",
                value_a="2024-04-01",
                value_b="2025-04-01",
            ),
        ),
    )

    projection = build_reliability_projection(reliability)

    assert projection["contradiction_detected"] is True
    assert projection["contradiction_count"] == 3
    assert projection["contradiction_pair_count"] == 2
    assert projection["contradiction_basis_types"] == ["effective_date", "version"]
    assert projection["reliability"]["evidence"]["contradiction"]["pairs"] == [
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "basis": "effective_date",
            "value_a": "2024-03-01",
            "value_b": "2025-03-01",
        },
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "basis": "version",
            "value_a": "v2",
            "value_b": "v3",
        },
        {
            "chunk_a_id": "c",
            "chunk_b_id": "d",
            "basis": "effective_date",
            "value_a": "2024-04-01",
            "value_b": "2025-04-01",
        },
    ]


def test_build_reliability_projection_uses_canonical_mirror_dedup_for_metrics() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="b",
                chunk_b_id="a",
                basis="effective_date",
                value_a="2025-03-01",
                value_b="2024-03-01",
            ),
        ),
    )

    projection = build_reliability_projection(reliability)

    assert projection["contradiction_detected"] is True
    assert projection["contradiction_count"] == 1
    assert projection["contradiction_pair_count"] == 1
    assert projection["contradiction_basis_types"] == ["effective_date"]


def test_build_reliability_projection_is_stable_for_empty_default_object() -> None:
    projection = build_reliability_projection(
        build_reliability_assessment(top_score=None, result_count=0)
    )

    assert projection["reliability"]["signals"] == [{"kind": "weak_recall"}]
    assert projection["reliability"]["evidence"] == {}
    assert projection["source_overlap_detected"] is False
    assert projection["source_overlap_pairs"] == []
    assert projection["contradiction_detected"] is False
    assert projection["contradiction_count"] == 0
    assert projection["contradiction_pair_count"] == 0
    assert projection["contradiction_basis_types"] == []
    assert projection["reliability"]["score"] == "low"
    assert projection["reliability"]["cap_reason"] is None


def test_search_result_bundle_default_reliability_matches_canonical_empty_state() -> None:
    from backend.search.service import SearchResultBundle

    bundle = SearchResultBundle(results=[])

    assert serialize_reliability(bundle.reliability) == serialize_reliability(
        build_reliability_assessment(top_score=None, result_count=0)
    )


def test_detect_source_overlaps_ignores_pairs_from_same_document() -> None:
    from backend.models import Embedding

    document_id = uuid.uuid4()
    first = Embedding(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1},
    )

    source_overlap_detected, source_overlap_pairs = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    )

    assert source_overlap_detected is False
    assert source_overlap_pairs == ()


def test_detect_source_overlaps_respects_similarity_threshold_boundary() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="alpha beta gamma",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="alpha beta gamma delta",
        metadata_json={"chunk_index": 1},
    )

    at_threshold = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.75,
    )
    above_threshold = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.76,
    )

    assert at_threshold[0] is True
    assert above_threshold[0] is False


# --- API tests (all mock OpenAI) ---


def test_search_no_embeddings(
    mock_openai_client: Mock, client: TestClient, db_session: Session
) -> None:
    """Given no embeddings in DB, POST /search → returns empty results list."""
    token = register_and_verify_user(client, db_session, email="noemb@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Emb Client"},
    )
    set_client_openai_key(client, token)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []


def test_search_route_traces_variant_summary(
    client: TestClient,
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

    token = register_and_verify_user(client, db_session, email="trace-search@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Search Client"},
    )
    set_client_openai_key(client, token)

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.search.routes.begin_trace", lambda **kwargs: fake_trace)
    monkeypatch.setattr(
        "backend.search.routes.search_similar_chunks_detailed",
        lambda **kwargs: SearchResultBundle(
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
        ),
    )

    response = client.post(
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
    client: TestClient,
    db_session: Session,
) -> None:
    """Create user, client, document, embedding; mock embed_query to return similar vector."""
    vec = [0.1] * 1536
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=vec)]

    token = register_and_verify_user(client, db_session, email="single@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Single Client"},
    )
    set_client_openai_key(client, token)
    md_content = b"# Doc\n\nRelevant content here."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("doc.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]
    client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(
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
    client: TestClient,
    db_session: Session,
) -> None:
    """3 embeddings with different similarity scores; results sorted DESC by similarity."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="multi@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Multi Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
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
    client: TestClient,
    db_session: Session,
) -> None:
    """Have > top_k embeddings, request top_k=2, only 2 results returned."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="topk@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "TopK Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "x", "top_k": 2},
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


def test_search_other_client_isolated(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Create embeddings for client A and B; search as user A → only A's results."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token_a = register_and_verify_user(client, db_session, email="isol_a@example.com")
    cl_a_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    set_client_openai_key(client, token_a)
    client_a_id = uuid.UUID(cl_a_resp.json()["id"])

    token_b = register_and_verify_user(client, db_session, email="isol_b@example.com")
    cl_b_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )
    client_b_id = uuid.UUID(cl_b_resp.json()["id"])

    doc_a = Document(
        client_id=client_a_id,
        filename="a.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Client A secret",
    )
    doc_b = Document(
        client_id=client_b_id,
        filename="b.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Client B secret",
    )
    db_session.add_all([doc_a, doc_b])
    db_session.commit()
    db_session.refresh(doc_a)
    db_session.refresh(doc_b)

    vec = [0.1] * 1536
    emb_a = Embedding(
        document_id=doc_a.id,
        chunk_text="Client A secret",
        vector=None,
        metadata_json={"chunk_index": 0, "vector": vec},
    )
    emb_b = Embedding(
        document_id=doc_b.id,
        chunk_text="Client B secret",
        vector=None,
        metadata_json={"chunk_index": 0, "vector": vec},
    )
    db_session.add_all([emb_a, emb_b])
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=vec)]

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"query": "secret", "top_k": 5},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["document_id"] == str(doc_a.id)
    assert "Client A" in results[0]["chunk_text"]


def test_search_requires_auth(client: TestClient) -> None:
    """No JWT → 401."""
    response = client.post(
        "/search",
        json={"query": "test", "top_k": 3},
    )
    assert response.status_code == 401


def test_search_requires_client(client: TestClient, db_session: Session) -> None:
    """Auth user without a client → 404."""
    token = register_and_verify_user(client, db_session, email="noclient@example.com")
    # Do NOT create a client

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test", "top_k": 3},
    )
    assert response.status_code == 404


def test_search_invalid_top_k(client: TestClient, db_session: Session) -> None:
    """top_k <= 0 → 422."""
    token = register_and_verify_user(client, db_session, email="invalid@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Invalid Client"},
    )
    set_client_openai_key(client, token)

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test", "top_k": 0},
    )
    assert response.status_code == 422


def test_search_empty_query_rejected(
    client: TestClient, db_session: Session
) -> None:
    """Empty query → 422."""
    token = register_and_verify_user(client, db_session, email="emptyq@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    set_client_openai_key(client, token)

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "", "top_k": 3},
    )
    assert response.status_code == 422


def test_search_default_top_k(
    mock_openai_client: Mock, client: TestClient, db_session: Session
) -> None:
    """Omit top_k → defaults to 3."""
    token = register_and_verify_user(client, db_session, email="default@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Default Client"},
    )
    set_client_openai_key(client, token)
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test"},
    )
    assert response.status_code == 200
    assert "results" in response.json()


# --- BM25 search unit tests ---


def test_bm25_search_chunks_finds_match(db_session) -> None:
    """bm25_search_chunks returns chunks relevant to query tokens."""
    from tests.test_models import _create_client, _create_user
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    user = _create_user(db_session, email="kw@example.com")
    cl = _create_client(db_session, user, name="KW Client")
    doc = Document(
        client_id=cl.id,
        filename="cors.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="CORS configuration",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="CORS settings: allow_origins, allow_methods",
        vector=None,
        metadata_json={"chunk_index": 0},
    )
    decoy_one = Embedding(
        document_id=doc.id,
        chunk_text="Billing export guide for invoices",
        vector=None,
        metadata_json={"chunk_index": 1},
    )
    decoy_two = Embedding(
        document_id=doc.id,
        chunk_text="Rotate API keys in dashboard settings",
        vector=None,
        metadata_json={"chunk_index": 2},
    )
    db_session.add_all([emb, decoy_one, decoy_two])
    db_session.commit()

    results = bm25_search_chunks(cl.id, "cors settings", top_k=5, db=db_session)
    assert len(results) == 3
    assert results[0][0].chunk_text == "CORS settings: allow_origins, allow_methods"
    assert 0 < results[0][1] <= 1.0
    assert results[0][1] > results[-1][1]


def test_bm25_signal_uses_overlap_fallback_when_raw_scores_are_flat() -> None:
    from backend.models import Embedding
    from backend.search.service import _bm25_score_candidates_with_signal

    matching = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="secret number explanation",
        metadata_json={"chunk_index": 0},
    )
    non_matching = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="billing invoice guide",
        metadata_json={"chunk_index": 1},
    )

    results, has_signal = _bm25_score_candidates_with_signal(
        [matching, non_matching],
        "secret",
        top_k=5,
    )

    assert has_signal is True
    assert [embedding.id for embedding, _ in results] == [matching.id]
    assert results[0][1] == 1.0


def test_search_low_vector_similarity_still_returns_chunk(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """SQLite shared pipeline still returns lexical matches even with zero vector confidence."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="fallback@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fallback Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
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
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="sqlitehybrid@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "SQLite Hybrid Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "cors configuration", "top_k": 2},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert "cors configuration settings" in [item["chunk_text"] for item in results]
    assert results[0]["chunk_text"] == "cors configuration settings"


def test_search_openai_unavailable_returns_503(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from openai import APIError

    token = register_and_verify_user(client, db_session, email="search503@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Search 503 Client"},
    )
    set_client_openai_key(client, token)
    mock_openai_client.embeddings.create.side_effect = APIError(
        "Service unavailable",
        request=Mock(),
        body=None,
    )

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "hello", "top_k": 3},
    )
    assert response.status_code == 503


def test_search_openai_timeout_returns_503(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from openai import APITimeoutError

    token = register_and_verify_user(client, db_session, email="search-timeout@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Search Timeout Client"},
    )
    set_client_openai_key(client, token)
    mock_openai_client.embeddings.create.side_effect = APITimeoutError(request=Mock())

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "hello", "top_k": 3},
    )
    assert response.status_code == 503


def test_search_skips_malformed_metadata_vectors(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="malformedvec@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Malformed Vec Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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
    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "content", "top_k": 5},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_search_skips_vector_with_wrong_dimension(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="wrongdim@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Wrong Dim Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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
    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything", "top_k": 3},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
