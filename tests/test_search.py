"""Tests for vector search API."""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from backend.search.service import cosine_similarity


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


@patch("backend.search.service.openai_client.embeddings.create")
def test_embed_query_uses_openai_client(mock_create: Mock) -> None:
    """embed_query calls OpenAI with correct model name."""
    from backend.search.service import embed_query

    mock_create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    embed_query("test query")
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    assert call_kwargs.kwargs.get("model") == "text-embedding-3-small"
    assert call_kwargs.kwargs.get("input") == "test query"


# --- API tests (all mock OpenAI) ---


@patch("backend.search.service.openai_client.embeddings.create")
def test_search_no_embeddings(mock_openai: Mock, client: TestClient) -> None:
    """Given no embeddings in DB, POST /search → returns empty results list."""
    reg = client.post(
        "/auth/register",
        json={"email": "noemb@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Emb Client"},
    )

    mock_openai.return_value.data = [Mock(embedding=[0.1] * 1536)]

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []


@patch("backend.embeddings.service.openai_client.embeddings.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_search_single_embedding_match(
    mock_search_openai: Mock,
    mock_emb_openai: Mock,
    client: TestClient,
) -> None:
    """Create user, client, document, embedding; mock embed_query to return similar vector."""
    vec = [0.1] * 1536
    mock_emb_openai.return_value.data = [Mock(embedding=vec)]
    mock_search_openai.return_value.data = [Mock(embedding=vec)]

    reg = client.post(
        "/auth/register",
        json={"email": "single@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Single Client"},
    )
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


@patch("backend.embeddings.service.openai_client.embeddings.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_search_multiple_results_sorted(
    mock_search_openai: Mock,
    mock_emb_openai: Mock,
    client: TestClient,
    db_session,
) -> None:
    """3 embeddings with different similarity scores; results sorted DESC by similarity."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "multi@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Multi Client"},
    )
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

    mock_search_openai.return_value.data = [Mock(embedding=query_vec)]

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


@patch("backend.embeddings.service.openai_client.embeddings.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_search_respects_top_k(
    mock_search_openai: Mock,
    mock_emb_openai: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Have > top_k embeddings, request top_k=2, only 2 results returned."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "topk@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "TopK Client"},
    )
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

    mock_search_openai.return_value.data = [Mock(embedding=vec)]

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "x", "top_k": 2},
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


@patch("backend.embeddings.service.openai_client.embeddings.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_search_other_client_isolated(
    mock_search_openai: Mock,
    mock_emb_openai: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Create embeddings for client A and B; search as user A → only A's results."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg_a = client.post(
        "/auth/register",
        json={"email": "isol_a@example.com", "password": "SecurePass1!"},
    )
    token_a = reg_a.json()["token"]
    cl_a_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    client_a_id = uuid.UUID(cl_a_resp.json()["id"])

    reg_b = client.post(
        "/auth/register",
        json={"email": "isol_b@example.com", "password": "SecurePass1!"},
    )
    token_b = reg_b.json()["token"]
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

    mock_search_openai.return_value.data = [Mock(embedding=vec)]

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


def test_search_requires_client(client: TestClient) -> None:
    """Auth user without a client → 404."""
    reg = client.post(
        "/auth/register",
        json={"email": "noclient@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    # Do NOT create a client

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test", "top_k": 3},
    )
    assert response.status_code == 404


def test_search_invalid_top_k(client: TestClient) -> None:
    """top_k <= 0 → 422."""
    reg = client.post(
        "/auth/register",
        json={"email": "invalid@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Invalid Client"},
    )

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test", "top_k": 0},
    )
    assert response.status_code == 422


def test_search_empty_query_rejected(client: TestClient) -> None:
    """Empty query → 422."""
    reg = client.post(
        "/auth/register",
        json={"email": "emptyq@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "", "top_k": 3},
    )
    assert response.status_code == 422


@patch("backend.search.service.openai_client.embeddings.create")
def test_search_default_top_k(mock_openai: Mock, client: TestClient) -> None:
    """Omit top_k → defaults to 3."""
    reg = client.post(
        "/auth/register",
        json={"email": "default@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Default Client"},
    )
    mock_openai.return_value.data = [Mock(embedding=[0.1] * 1536)]

    response = client.post(
        "/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "test"},
    )
    assert response.status_code == 200
    assert "results" in response.json()
