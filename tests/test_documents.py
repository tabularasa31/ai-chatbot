"""Tests for document upload and parsing API."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user


def _make_minimal_pdf() -> bytes:
    """Create a minimal valid PDF in memory for testing."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_upload_pdf_success(client: TestClient, db_session: Session) -> None:
    """Upload a small PDF, get DocumentResponse back, status=ready."""
    token = register_and_verify_user(client, db_session, email="pdf@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "PDF Client"},
    )
    pdf_bytes = _make_minimal_pdf()
    response = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["filename"] == "test.pdf"
    assert data["file_type"] == "pdf"
    assert data["status"] == "ready"
    assert "created_at" in data
    assert "updated_at" in data


def test_upload_markdown_success(client: TestClient, db_session: Session) -> None:
    """Upload .md file, status=ready."""
    token = register_and_verify_user(client, db_session, email="md@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "MD Client"},
    )
    md_content = b"# Test\n\nThis is a test document."
    response = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.md", md_content, "text/markdown")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "test.md"
    assert data["file_type"] == "markdown"
    assert data["status"] == "ready"


def test_upload_swagger_success(client: TestClient, db_session: Session) -> None:
    """Upload valid OpenAPI JSON, status=ready."""
    token = register_and_verify_user(client, db_session, email="swagger@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Swagger Client"},
    )
    swagger_content = b'{"openapi":"3.0.0","info":{"title":"Test API","version":"1.0"},"paths":{"/test":{"get":{"description":"Test endpoint"}}}}'
    response = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("api.json", swagger_content, "application/json")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "api.json"
    assert data["file_type"] == "swagger"
    assert data["status"] == "ready"


def test_upload_unsupported_type(client: TestClient, db_session: Session) -> None:
    """Upload .exe → 400."""
    token = register_and_verify_user(client, db_session, email="exe@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Exe Client"},
    )
    response = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_upload_too_large(client: TestClient, db_session: Session) -> None:
    """Upload >50MB file → 400."""
    token = register_and_verify_user(client, db_session, email="large@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Large Client"},
    )
    # 51 MB
    large_content = b"x" * (51 * 1024 * 1024)
    response = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("huge.pdf", large_content, "application/pdf")},
    )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


def test_upload_no_client(client: TestClient, db_session: Session) -> None:
    """Upload without creating client first → 404."""
    token = register_and_verify_user(client, db_session, email="noclient@example.com")
    md_content = b"# Test"
    response = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.md", md_content, "text/markdown")},
    )
    assert response.status_code == 404
    assert "client" in response.json()["detail"].lower()


def test_upload_unauthenticated(client: TestClient) -> None:
    """No JWT → 401."""
    md_content = b"# Test"
    response = client.post(
        "/documents",
        files={"file": ("test.md", md_content, "text/markdown")},
    )
    assert response.status_code == 401


def test_list_documents_empty(client: TestClient, db_session: Session) -> None:
    """Get documents when none uploaded → empty list."""
    token = register_and_verify_user(client, db_session, email="empty@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    response = client.get(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "documents" in data
    assert data["documents"] == []


def test_list_documents(client: TestClient, db_session: Session) -> None:
    """Upload 2 docs, get list → 2 items."""
    token = register_and_verify_user(client, db_session, email="list@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "List Client"},
    )
    md1 = b"# Doc 1"
    md2 = b"# Doc 2"
    client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("doc1.md", md1, "text/markdown")},
    )
    client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("doc2.md", md2, "text/markdown")},
    )
    response = client.get(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["documents"]) == 2
    filenames = {d["filename"] for d in data["documents"]}
    assert filenames == {"doc1.md", "doc2.md"}


def test_get_document_detail(client: TestClient, db_session: Session) -> None:
    """Get single document, verify parsed_text preview."""
    token = register_and_verify_user(client, db_session, email="detail@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Detail Client"},
    )
    md_content = b"# Test\n\nThis is a test document with some content."
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("detail.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]
    response = client.get(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(doc_id)
    assert data["filename"] == "detail.md"
    assert "parsed_text" in data
    assert "Test" in (data["parsed_text"] or "")
    assert "test document" in (data["parsed_text"] or "")


def test_get_document_wrong_user(client: TestClient, db_session: Session) -> None:
    """User B tries to get user A's document → 404."""
    token_a = register_and_verify_user(client, db_session, email="userA@example.com")
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

    token_b = register_and_verify_user(client, db_session, email="userB@example.com")

    response = client.get(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_delete_document_success(client: TestClient, db_session: Session) -> None:
    """Delete document → 204, verify gone."""
    token = register_and_verify_user(client, db_session, email="del@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Del Client"},
    )
    md_content = b"# To Delete"
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("todel.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    response = client.delete(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    get_resp = client.get(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 404


def test_delete_document_wrong_user(client: TestClient, db_session: Session) -> None:
    """User B tries to delete user A's document → 404."""
    token_a = register_and_verify_user(client, db_session, email="delA@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    md_content = b"# Protected"
    upload_resp = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("protected.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    token_b = register_and_verify_user(client, db_session, email="delB@example.com")

    response = client.delete(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
