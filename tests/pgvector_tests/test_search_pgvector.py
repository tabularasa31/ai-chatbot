"""
pgvector integration tests.

These tests require a real PostgreSQL instance with the pgvector extension.
Run with: pytest tests/pgvector_tests/ -m pgvector

Configure the target instance via environment variables:
  PG_HOST (default: localhost), PG_PORT (default: 5432),
  PG_USER (default: postgres), PG_PASSWORD (default: postgres)
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key


# ---------------------------------------------------------------------------
# Helper — insert an Embedding with a real vector directly into PG
# ---------------------------------------------------------------------------


def _insert_embedding(db: Session, document_id: uuid.UUID, chunk_text: str, vector: list[float]) -> None:
    from backend.models import Embedding

    emb = Embedding(
        document_id=document_id,
        chunk_text=chunk_text,
        vector=vector,
        metadata_json={"chunk_index": 0},
    )
    db.add(emb)
    db.commit()


def _make_document(db: Session, tenant_id: uuid.UUID, filename: str = "doc.md") -> uuid.UUID:
    from backend.models import Document, DocumentStatus, DocumentType

    doc = Document(
        tenant_id=tenant_id,
        filename=filename,
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc.id


# ---------------------------------------------------------------------------
# Smoke test — pgvector extension is available
# ---------------------------------------------------------------------------


@pytest.mark.pgvector
def test_pgvector_extension_enabled(pg_engine) -> None:
    """pgvector extension must be installed in the test database."""
    import sqlalchemy as sa

    with pg_engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        row = result.fetchone()
    assert row is not None, "pgvector extension is not installed"
    assert row[0] == "vector"


# ---------------------------------------------------------------------------
# _pgvector_search — native cosine distance via HNSW
# ---------------------------------------------------------------------------


@pytest.mark.pgvector
def test_pgvector_search_returns_results(
    mock_openai_client: Mock,
    pg_db_session: Session,
) -> None:
    """_pgvector_search returns embeddings ordered by cosine proximity."""
    from tests.test_models import _create_client, _create_user
    from backend.search.service import _pgvector_search

    user = _create_user(pg_db_session, email="pv_basic@example.com")
    cl = _create_client(pg_db_session, user, name="PV Basic")

    doc_id = _make_document(pg_db_session, cl.id)

    query_vec = [1.0] + [0.0] * 1535
    close_vec = [0.99, 0.1] + [0.0] * 1534
    far_vec = [0.0, 1.0] + [0.0] * 1534

    _insert_embedding(pg_db_session, doc_id, "close chunk", close_vec)
    _insert_embedding(pg_db_session, doc_id, "far chunk", far_vec)

    results = _pgvector_search(cl.id, query_vec, top_k=5, db=pg_db_session)

    assert len(results) == 2
    texts = [emb.chunk_text for emb, _ in results]
    # closest should be first
    assert texts[0] == "close chunk"
    assert texts[1] == "far chunk"
    # scores are cosine similarity in [0, 1]
    scores = [score for _, score in results]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores[0] > scores[1]


@pytest.mark.pgvector
def test_pgvector_search_respects_client_isolation(
    mock_openai_client: Mock,
    pg_db_session: Session,
) -> None:
    """_pgvector_search must not leak embeddings across tenants."""
    from tests.test_models import _create_client, _create_user
    from backend.search.service import _pgvector_search

    user_a = _create_user(pg_db_session, email="pv_iso_a@example.com")
    user_b = _create_user(pg_db_session, email="pv_iso_b@example.com")
    cl_a = _create_client(pg_db_session, user_a, name="PV Iso A")
    cl_b = _create_client(pg_db_session, user_b, name="PV Iso B")

    doc_a = _make_document(pg_db_session, cl_a.id, "a.md")
    doc_b = _make_document(pg_db_session, cl_b.id, "b.md")

    vec = [0.5] + [0.0] * 1535
    _insert_embedding(pg_db_session, doc_a, "tenant A secret", vec)
    _insert_embedding(pg_db_session, doc_b, "tenant B secret", vec)

    results = _pgvector_search(cl_a.id, vec, top_k=10, db=pg_db_session)

    assert len(results) == 1
    assert results[0][0].chunk_text == "tenant A secret"


@pytest.mark.pgvector
def test_pgvector_search_empty_when_no_vector(
    mock_openai_client: Mock,
    pg_db_session: Session,
) -> None:
    """_pgvector_search skips rows with NULL vector column."""
    from tests.test_models import _create_client, _create_user
    from backend.models import Embedding
    from backend.search.service import _pgvector_search

    user = _create_user(pg_db_session, email="pv_nullvec@example.com")
    cl = _create_client(pg_db_session, user, name="PV NullVec")
    doc_id = _make_document(pg_db_session, cl.id)

    # Insert with NULL vector
    emb = Embedding(
        document_id=doc_id,
        chunk_text="no vector here",
        vector=None,
        metadata_json={},
    )
    pg_db_session.add(emb)
    pg_db_session.commit()

    query_vec = [1.0] + [0.0] * 1535
    results = _pgvector_search(cl.id, query_vec, top_k=5, db=pg_db_session)

    assert results == []


# ---------------------------------------------------------------------------
# search_similar_chunks — hybrid BM25 + RRF path on PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.pgvector
def test_hybrid_search_uses_pgvector_path(
    mock_openai_client: Mock,
    pg_db_session: Session,
) -> None:
    """search_similar_chunks must take the pgvector branch (not SQLite fallback) on PG."""
    from tests.test_models import _create_client, _create_user
    from backend.search.service import search_similar_chunks

    user = _create_user(pg_db_session, email="hybrid_basic@example.com")
    cl = _create_client(pg_db_session, user, name="Hybrid Basic")
    doc_id = _make_document(pg_db_session, cl.id)

    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    vec = [0.9, 0.1] + [0.0] * 1534
    _insert_embedding(pg_db_session, doc_id, "hybrid test chunk", vec)

    results = search_similar_chunks(
        cl.id, "hybrid test", top_k=3, db=pg_db_session, api_key="sk-test"
    )

    assert len(results) == 1
    assert results[0][0].chunk_text == "hybrid test chunk"


@pytest.mark.pgvector
def test_hybrid_search_bm25_rrf_boosts_keyword_match(
    mock_openai_client: Mock,
    pg_db_session: Session,
) -> None:
    """
    Hybrid search returns both vector-close and keyword-relevant chunks.
    BM25 + RRF merges the results: both chunks should appear in top_k=2.
    """
    from tests.test_models import _create_client, _create_user
    from backend.search.service import search_similar_chunks

    user = _create_user(pg_db_session, email="hybrid_rrf@example.com")
    cl = _create_client(pg_db_session, user, name="Hybrid RRF")
    doc_id = _make_document(pg_db_session, cl.id)

    # Chunk A: perfectly aligned with query vector, no keyword match
    vec_a = [1.0] + [0.0] * 1535
    _insert_embedding(pg_db_session, doc_id, "unrelated words xyz qrs", vec_a)

    # Chunk B: same direction but slightly rotated, exact keyword match for query
    vec_b = [0.9, 0.1] + [0.0] * 1534
    _insert_embedding(pg_db_session, doc_id, "cors configuration settings", vec_b)

    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    results = search_similar_chunks(
        cl.id, "cors configuration", top_k=2, db=pg_db_session, api_key="sk-test"
    )

    # Both chunks should be returned — hybrid search merges vector and BM25 signals
    assert len(results) == 2
    result_texts = [emb.chunk_text for emb, _ in results]
    assert "cors configuration settings" in result_texts
    assert "unrelated words xyz qrs" in result_texts


@pytest.mark.pgvector
def test_hybrid_search_limits_results_with_mixed_candidates(
    mock_openai_client: Mock,
    pg_db_session: Session,
) -> None:
    """Hybrid search keeps top_k when candidates mix keyword and weak/noisy chunks."""
    from tests.test_models import _create_client, _create_user
    from backend.search.service import search_similar_chunks

    user = _create_user(pg_db_session, email="hybrid_mixed@example.com")
    cl = _create_client(pg_db_session, user, name="Hybrid Mixed")
    doc_id = _make_document(pg_db_session, cl.id)

    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    _insert_embedding(pg_db_session, doc_id, "cors origin setting in dashboard", [0.95, 0.1] + [0.0] * 1534)
    _insert_embedding(pg_db_session, doc_id, "random unrelated payload alpha beta", [0.6, 0.6] + [0.0] * 1534)
    _insert_embedding(pg_db_session, doc_id, "another noisy chunk", [0.2, 0.95] + [0.0] * 1534)

    results = search_similar_chunks(
        cl.id, "cors setting", top_k=2, db=pg_db_session, api_key="sk-test"
    )
    assert len(results) == 2
    texts = [emb.chunk_text for emb, _ in results]
    assert any("cors" in t for t in texts)


@pytest.mark.pgvector
def test_hybrid_search_symmetric_bm25_evaluates_extra_lexical_variants_on_pg(
    mock_openai_client: Mock,
    pg_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-EN query routes BM25 through the EN rewrite, producing lexical signal against English corpus."""
    from tests.test_models import _create_client, _create_user
    from backend.search.service import search_similar_chunks_detailed

    user = _create_user(pg_db_session, email="hybrid_symmetric@example.com")
    cl = _create_client(pg_db_session, user, name="Hybrid Symmetric")
    doc_id = _make_document(pg_db_session, cl.id)

    query_vec = [0.5] + [0.0] * 1535
    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [query_vec for _ in queries],
    )
    # Simulate query rewrite: Cyrillic query → EN keyword phrase
    monkeypatch.setattr(
        "backend.search.service._rewrite_query_for_retrieval",
        lambda query, **kwargs: "reset password instructions",
    )

    _insert_embedding(pg_db_session, doc_id, "unrelated foo content", [0.5] + [0.0] * 1535)
    _insert_embedding(
        pg_db_session,
        doc_id,
        "reset password instructions",
        [0.5] + [0.0] * 1535,
    )

    # Russian query — non-EN, BM25 must use the EN rewrite to find lexical matches
    result = search_similar_chunks_detailed(
        cl.id,
        "сброс пароля",
        top_k=2,
        db=pg_db_session,
        api_key="sk-test",
    )

    assert result.bm25_query_variant_count == 1
    assert result.bm25_variant_eval_count == 1
    assert result.has_lexical_signal is True
    assert result.best_keyword_score is not None
    result_texts = [emb.chunk_text for emb, _ in result.results]
    assert "reset password instructions" in result_texts


@pytest.mark.pgvector
def test_hybrid_search_symmetric_bm25_can_add_work_without_changing_final_results(
    mock_openai_client: Mock,
    pg_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EN query uses original query for BM25; ranking is stable with or without a rewrite variant."""
    from tests.test_models import _create_client, _create_user
    from backend.search.service import search_similar_chunks_detailed

    user = _create_user(pg_db_session, email="hybrid_control@example.com")
    cl = _create_client(pg_db_session, user, name="Hybrid Control")
    doc_id = _make_document(pg_db_session, cl.id)

    query_vec = [1.0] + [0.0] * 1535
    monkeypatch.setattr(
        "backend.search.service.embed_queries",
        lambda queries, **kwargs: [query_vec for _ in queries],
    )

    _insert_embedding(
        pg_db_session,
        doc_id,
        "cors settings allow origins",
        [0.99, 0.01] + [0.0] * 1534,
    )
    _insert_embedding(
        pg_db_session,
        doc_id,
        "rotate api keys in dashboard",
        [0.7, 0.3] + [0.0] * 1534,
    )

    # EN query with no rewrite (None returned) — BM25 uses the original query
    monkeypatch.setattr(
        "backend.search.service._rewrite_query_for_retrieval",
        lambda query, **kwargs: None,
    )
    no_rewrite = search_similar_chunks_detailed(
        cl.id,
        "cors settings",
        top_k=2,
        db=pg_db_session,
        api_key="sk-test",
    )

    # EN query with a rewrite present — BM25 still uses the original EN query (rewrite ignored for EN)
    monkeypatch.setattr(
        "backend.search.service._rewrite_query_for_retrieval",
        lambda query, **kwargs: "cors origin configuration allow list",
    )
    with_rewrite = search_similar_chunks_detailed(
        cl.id,
        "cors settings",
        top_k=2,
        db=pg_db_session,
        api_key="sk-test",
    )

    # Both runs use 1 BM25 query variant (the original EN query)
    assert no_rewrite.bm25_query_variant_count == 1
    assert with_rewrite.bm25_query_variant_count == 1
    # Ranking is stable regardless of the rewrite
    assert [embedding.id for embedding, _ in with_rewrite.results] == [
        embedding.id for embedding, _ in no_rewrite.results
    ]


# ---------------------------------------------------------------------------
# Full HTTP path on real PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.pgvector
def test_search_endpoint_uses_pgvector(
    mock_openai_client: Mock,
    pg_client: TestClient,
    pg_db_session: Session,
) -> None:
    """POST /search returns results going through pgvector path end-to-end."""
    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=query_vec)]

    token = register_and_verify_user(pg_client, pg_db_session, email="ep_pv@example.com")
    cl_resp = pg_client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "EP PV Tenant"},
    )
    assert cl_resp.status_code in (200, 201)
    set_client_openai_key(pg_client, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc_id = _make_document(pg_db_session, tenant_id)
    vec = [0.9, 0.1] + [0.0] * 1534
    _insert_embedding(pg_db_session, doc_id, "endpoint pgvector chunk", vec)
    # Flush so HTTP handler sees the row
    pg_db_session.commit()

    response = pg_client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "pgvector endpoint", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) >= 1
    assert any("pgvector" in r["chunk_text"] for r in data["results"])
