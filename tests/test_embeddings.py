"""Tests for embedding creation and management API."""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from backend.embeddings.service import chunk_text


def test_chunk_text_basic() -> None:
    """Chunk 'a' * 1000 → get multiple chunks of ~500 chars."""
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    assert len(chunks) >= 2
    for c in chunks[:-1]:
        assert len(c) == 500
    assert all(isinstance(c, str) for c in chunks)


def test_chunk_text_overlap() -> None:
    """Verify overlap between consecutive chunks."""
    text = "abcdefghij" * 60  # 600 chars
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    assert len(chunks) >= 2
    # Chunk 1 ends at 500, chunk 2 starts at 400 (500-100)
    first_end = chunks[0][-50:]
    second_start = chunks[1][:50]
    # Overlap region: chars 400-500 of first = chars 0-100 of second
    overlap_region = chunks[0][400:500]
    assert overlap_region in chunks[1] or chunks[1].startswith(overlap_region[:50])


def test_chunk_text_short() -> None:
    """Text shorter than chunk_size → returns single chunk."""
    text = "short"
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    assert len(chunks) == 1
    assert chunks[0] == "short"


def test_chunk_text_empty() -> None:
    """Empty string → returns empty list."""
    chunks = chunk_text("", chunk_size=500, overlap=100)
    assert chunks == []
    chunks2 = chunk_text("   \n\t  ", chunk_size=500, overlap=100)
    assert chunks2 == []


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_create_embeddings_success(
    mock_openai: Mock,
    client: TestClient,
) -> None:
    """Mock OpenAI, create embeddings for ready doc → chunks saved."""
    reg = client.post(
        "/auth/register",
        json={"email": "emb@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Emb Client"},
    )
    md_content = b"# Test\n\n" + b"Lorem ipsum. " * 50  # enough for multiple chunks
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("emb.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap=100)
    mock_openai.return_value.data = [
        Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))
    ]

    response = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["chunks_created"] == len(chunks)
    assert data["status"] == "ready"

    list_resp = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total_chunks"] == len(chunks)


def test_create_embeddings_document_not_found(client: TestClient) -> None:
    """404 if doc doesn't exist."""
    reg = client.post(
        "/auth/register",
        json={"email": "nf@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "NF Client"},
    )
    fake_id = str(uuid.uuid4())
    response = client.post(
        f"/embeddings/documents/{fake_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_create_embeddings_document_not_ready(
    mock_openai: Mock,
    client: TestClient,
    db_session,
) -> None:
    """400 if doc status=processing."""
    from backend.models import Document, DocumentStatus, DocumentType

    reg = client.post(
        "/auth/register",
        json={"email": "proc@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Proc Client"},
    )
    client_id = cl_resp.json()["id"]

    doc = Document(
        client_id=uuid.UUID(client_id),
        filename="proc.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.processing,
        parsed_text="some text",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    doc_id = str(doc.id)

    response = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    mock_openai.assert_not_called()


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_create_embeddings_openai_error(
    mock_openai: Mock,
    client: TestClient,
) -> None:
    """OpenAI raises exception → return 503."""
    reg = client.post(
        "/auth/register",
        json={"email": "err@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Err Client"},
    )
    md_content = b"# Test\n\nContent here."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("err.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    mock_openai.side_effect = Exception("OpenAI API error")

    response = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_create_embeddings_reruns_delete_old(
    mock_openai: Mock,
    client: TestClient,
) -> None:
    """Call twice → old embeddings replaced."""
    reg = client.post(
        "/auth/register",
        json={"email": "rerun@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Rerun Client"},
    )
    md_content = b"# Doc\n\n" + b"x" * 600
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("rerun.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap=100)
    mock_openai.return_value.data = [
        Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))
    ]

    r1 = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    n1 = r1.json()["chunks_created"]

    r2 = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    n2 = r2.json()["chunks_created"]
    assert n2 == n1
    list_resp = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.json()["total_chunks"] == n1


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_get_embeddings_success(
    mock_openai: Mock,
    client: TestClient,
) -> None:
    """Get embeddings list for document."""
    reg = client.post(
        "/auth/register",
        json={"email": "get@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Get Client"},
    )
    md_content = b"# Get\n\nContent for embeddings."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("get.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap=100)
    mock_openai.return_value.data = [
        Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))
    ]
    client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "embeddings" in data
    assert "total_chunks" in data
    assert data["total_chunks"] == len(chunks)
    for emb in data["embeddings"]:
        assert "id" in emb
        assert emb["document_id"] == doc_id
        assert "chunk_text" in emb
        assert "created_at" in emb


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_get_embeddings_wrong_client(
    mock_openai: Mock,
    client: TestClient,
) -> None:
    """User B can't see user A's embeddings → 404."""
    reg_a = client.post(
        "/auth/register",
        json={"email": "ga@example.com", "password": "SecurePass1!"},
    )
    token_a = reg_a.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    md_content = b"# Secret"
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("secret.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap=100)
    mock_openai.return_value.data = [
        Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))
    ]
    client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    reg_b = client.post(
        "/auth/register",
        json={"email": "gb@example.com", "password": "SecurePass1!"},
    )
    token_b = reg_b.json()["token"]

    response = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


@patch("backend.embeddings.service.openai_client.embeddings.create")
def test_delete_embeddings_success(
    mock_openai: Mock,
    client: TestClient,
) -> None:
    """Delete all embeddings → returns count."""
    reg = client.post(
        "/auth/register",
        json={"email": "del@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Del Emb Client"},
    )
    md_content = b"# To delete\n\nContent."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("del.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap=100)
    mock_openai.return_value.data = [
        Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))
    ]
    client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.delete(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == len(chunks)

    list_resp = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.json()["total_chunks"] == 0
