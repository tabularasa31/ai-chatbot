"""Tests for embedding creation and management API."""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.embeddings.service import chunk_text
from backend.models import Embedding


def test_chunk_text_basic() -> None:
    """Many short sentences → multiple chunks bounded by chunk_size."""
    text = " ".join([f"block{i}." for i in range(120)])
    chunks = chunk_text(text, chunk_size=500, overlap_sentences=1)
    assert len(chunks) >= 2
    for c in chunks:
        assert set(c.keys()) == {"text", "chunk_index", "char_offset", "char_end"}


def test_chunk_text_overlap() -> None:
    """Last sentence of chunk N repeats at start of chunk N+1 (overlap_sentences=1)."""
    text = " ".join([f"line{n}." for n in range(80)])
    chunks = chunk_text(text, chunk_size=80, overlap_sentences=1)
    assert len(chunks) >= 2
    tail = chunks[0]["text"].strip().rsplit(maxsplit=1)[-1]
    assert chunks[1]["text"].strip().startswith(tail)


def test_chunk_text_short() -> None:
    """Text shorter than chunk_size → returns single chunk."""
    text = "short"
    chunks = chunk_text(text, chunk_size=500, overlap_sentences=1)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "short"


def test_chunk_text_empty() -> None:
    """Empty string → returns empty list."""
    chunks = chunk_text("", chunk_size=500, overlap_sentences=1)
    assert chunks == []
    chunks2 = chunk_text("   \n\t  ", chunk_size=500, overlap_sentences=1)
    assert chunks2 == []


def test_chunk_text_hello_world() -> None:
    """Single sentence returns one dict with expected keys and offsets."""
    text = "Hello world."
    chunks = chunk_text(text, chunk_size=500, overlap_sentences=1)
    assert len(chunks) >= 1
    c0 = chunks[0]
    for key in ("text", "chunk_index", "char_offset", "char_end"):
        assert key in c0
    assert c0["chunk_index"] == 0
    assert text[c0["char_offset"] : c0["char_end"]] == c0["text"]


def test_chunk_text_tokens_are_whole_words() -> None:
    """Chunks join full sentences only — no mid-token cuts at boundaries (FI-009 checklist)."""
    text = " ".join([f"w{n:03d}." for n in range(60)])
    chunks = chunk_text(text, chunk_size=40, overlap_sentences=1)
    assert len(chunks) >= 2
    for c in chunks:
        for token in c["text"].split():
            assert token.startswith("w") and token.endswith(".")


def test_chunk_text_oversized_single_sentence_stays_one_chunk() -> None:
    """Soft chunk_size: one long sentence without inner boundaries is not split."""
    text = "n" * 800
    chunks = chunk_text(text, chunk_size=100, overlap_sentences=1)
    assert len(chunks) == 1
    assert chunks[0]["text"] == text


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_success(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Mock OpenAI, create embeddings for ready doc → chunks saved."""
    token = register_and_verify_user(client, db_session, email="emb@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Emb Client"},
    )
    set_client_openai_key(client, token)
    md_content = b"# Test\n\n" + b"Lorem ipsum. " * 50  # enough for multiple chunks
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("emb.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    # Use the same chunking config as the service uses for markdown documents
    from backend.embeddings.service import CHUNKING_CONFIG
    md_cfg = CHUNKING_CONFIG.get("markdown", {"chunk_size": 700, "overlap_sentences": 1})
    chunks = chunk_text(md_content.decode(), **md_cfg)
    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))]
    )
    mock_get_openai.return_value = mock_client

    response = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["status"] == "embedding"

    # Background task ran synchronously; verify chunks were created via the list endpoint
    list_resp = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total_chunks"] == len(chunks)

    # Verify metadata fields via a fresh session (background task uses its own session)
    from backend.core.db import SessionLocal
    with SessionLocal() as fresh_db:
        rows = (
            fresh_db.query(Embedding)
            .filter(Embedding.document_id == uuid.UUID(doc_id))
            .order_by(Embedding.created_at.asc())
            .all()
        )
    assert len(rows) == len(chunks)
    for row in rows:
        m = row.metadata_json
        assert set(m.keys()) >= {
            "chunk_index",
            "char_offset",
            "char_end",
            "filename",
            "file_type",
        }
        assert m["filename"] == "emb.md"
        assert m["file_type"] == "markdown"


def test_create_embeddings_document_not_found(
    client: TestClient, db_session: Session
) -> None:
    """404 if doc doesn't exist."""
    token = register_and_verify_user(client, db_session, email="nf@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "NF Client"},
    )
    set_client_openai_key(client, token)
    fake_id = str(uuid.uuid4())
    response = client.post(
        f"/embeddings/documents/{fake_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_document_not_ready(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """400 if doc status=processing."""
    from backend.models import Document, DocumentStatus, DocumentType

    token = register_and_verify_user(client, db_session, email="proc@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Proc Client"},
    )
    set_client_openai_key(client, token)
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
    mock_get_openai.assert_not_called()


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_openai_error(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """OpenAI raises exception → return 503."""
    token = register_and_verify_user(client, db_session, email="err@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Err Client"},
    )
    set_client_openai_key(client, token)
    md_content = b"# Test\n\nContent here."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("err.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    mock_client = Mock()
    mock_client.embeddings.create.side_effect = Exception("OpenAI API error")
    mock_get_openai.return_value = mock_client

    response = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    # Background task ran synchronously and set doc status to "error" after OpenAI failure
    doc_resp = client.get(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert doc_resp.status_code == 200
    assert doc_resp.json()["status"] == "error"


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_reruns_delete_old(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Call twice → old embeddings replaced."""
    token = register_and_verify_user(client, db_session, email="rerun@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Rerun Client"},
    )
    set_client_openai_key(client, token)
    md_content = b"# Doc\n\n" + b"x" * 600
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("rerun.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap_sentences=1)
    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))]
    )
    mock_get_openai.return_value = mock_client

    r1 = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 202

    r2 = client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 202
    list_resp = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.json()["total_chunks"] == len(chunks)


@patch("backend.embeddings.service.get_openai_client")
def test_get_embeddings_success(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Get embeddings list for document."""
    token = register_and_verify_user(client, db_session, email="get@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Get Client"},
    )
    set_client_openai_key(client, token)
    md_content = b"# Get\n\nContent for embeddings."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("get.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap_sentences=1)
    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))]
    )
    mock_get_openai.return_value = mock_client
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


@patch("backend.embeddings.service.get_openai_client")
def test_get_embeddings_wrong_client(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """User B can't see user A's embeddings → 404."""
    token_a = register_and_verify_user(client, db_session, email="ga@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    set_client_openai_key(client, token_a)
    md_content = b"# Secret"
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("secret.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap_sentences=1)
    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))]
    )
    mock_get_openai.return_value = mock_client
    client.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    token_b = register_and_verify_user(client, db_session, email="gb@example.com")

    response = client.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


@patch("backend.embeddings.service.get_openai_client")
def test_delete_embeddings_success(
    mock_get_openai: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Delete all embeddings → returns count."""
    token = register_and_verify_user(client, db_session, email="del@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Del Emb Client"},
    )
    set_client_openai_key(client, token)
    md_content = b"# To delete\n\nContent."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("del.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap_sentences=1)
    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))]
    )
    mock_get_openai.return_value = mock_client
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
