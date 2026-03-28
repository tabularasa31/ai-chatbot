"""Tests for vector search API."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.search.service import (
    apply_script_boost,
    bm25_search_chunks,
    compute_reliability_score,
    cosine_similarity,
    embed_queries,
    embed_queries_with_stats,
    detect_query_script_bucket,
    detect_conflicts,
    detect_source_overlaps,
    expand_query,
    mmr_select,
    rerank_candidates,
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
    assert bundle.vector_search_call_count == 3
    assert bundle.extra_vector_search_calls == 2
    assert query_embedding_span.output == {
        "embedded_query_count": 3,
        "extra_embedded_queries": 2,
        "embedding_api_request_count": 1,
        "duration_ms": query_embedding_span.output["duration_ms"],
    }
    assert vector_span.output is not None
    assert vector_span.output["vector_search_call_count"] == 3
    assert vector_span.output["extra_vector_search_calls"] == 2
    assert bundle.retrieval_duration_ms >= bundle.query_embedding_duration_ms
    assert bundle.retrieval_duration_ms >= bundle.vector_search_duration_ms


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

    conflicts_found, conflict_pairs, reliability_cap = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    )

    assert conflicts_found is True
    assert reliability_cap == "source_overlap"
    assert conflict_pairs == [
        {
            "chunk_a_id": str(first.id),
            "chunk_b_id": str(second.id),
            "similarity": 0.8333,
            "signal_type": "cross_document_overlap",
            "confirmed_by_llm": False,
        }
    ]


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

    conflicts_found, conflict_pairs, reliability_cap = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    )

    assert conflicts_found is False
    assert conflict_pairs == []
    assert reliability_cap is None


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


def test_detect_conflicts_aliases_source_overlap_heuristic() -> None:
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

    assert detect_conflicts(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    ) == detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    )


def test_compute_reliability_score_uses_conflicts_and_top_score() -> None:
    assert compute_reliability_score(top_score=0.9, conflicts_found=False, result_count=5) == "high"
    assert compute_reliability_score(top_score=0.9, conflicts_found=True, result_count=5) == "medium"
    assert compute_reliability_score(top_score=0.2, conflicts_found=False, result_count=5) == "low"
    assert compute_reliability_score(top_score=None, conflicts_found=False, result_count=0) == "low"


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
            vector_search_call_count=3,
            extra_vector_search_calls=2,
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
    assert fake_trace.update_calls == [
        {
            "output": {"result_count": 0},
            "metadata": {
                "route": "/search",
                "search_result_count": 0,
                "variant_mode": "multi",
                "query_variant_count": 3,
                "extra_embedded_queries": 2,
                "extra_vector_search_calls": 2,
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
    assert data["results"][0]["similarity"] == 1.0
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
    db_session.add(emb)
    db_session.commit()

    results = bm25_search_chunks(cl.id, "cors настройка", top_k=5, db=db_session)
    assert len(results) == 1
    assert results[0][0].chunk_text == "CORS settings: allow_origins, allow_methods"
    assert 0 < results[0][1] <= 1.0


def test_search_low_vector_similarity_still_returns_chunk(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """SQLite cosine path: orthogonal query/chunk vectors still returns chunk with score 0."""
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

    # Query vector orthogonal to stored → vector sim = 0 → fallback to keyword
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
