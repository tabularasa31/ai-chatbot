"""Tests for vector search API."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.search.service import cosine_similarity, keyword_search_chunks


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


def test_embed_query_uses_openai_client(mock_openai_client: Mock) -> None:
    """embed_query calls OpenAI with correct model name."""
    from backend.search.service import embed_query

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    embed_query("test query", api_key="sk-test")
    mock_openai_client.embeddings.create.assert_called_once()
    call_kwargs = mock_openai_client.embeddings.create.call_args
    assert call_kwargs.kwargs.get("model") == "text-embedding-3-small"
    assert call_kwargs.kwargs.get("input") == "test query"


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


# --- Keyword search unit tests ---


def test_keyword_search_chunks_finds_match(db_session) -> None:
    """keyword_search_chunks returns chunks containing query keywords."""
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

    results = keyword_search_chunks(cl.id, "cors настройка", top_k=5, db=db_session)
    assert len(results) == 1
    assert results[0][0].chunk_text == "CORS settings: allow_origins, allow_methods"
    assert results[0][1] >= 1.0  # at least "cors" matched


def test_search_keyword_fallback_when_vector_low(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Short/vague query with low vector similarity → keyword fallback finds CORS chunk."""
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
