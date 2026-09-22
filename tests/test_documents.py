"""Tests for document upload and parsing API."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from backend.auth.service import create_token_for_user
from backend.tenants.service import create_tenant
from backend.documents import embedder as embedder_mod
from backend.documents import http_client as http_client_mod
from backend.documents.constants import KNOWLEDGE_DOCUMENT_CAPACITY
from backend.core.security import hash_password
from backend.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    QuickAnswer,
    SourceSchedule,
    SourceStatus,
    UrlSource,
    UrlSourceRun,
)
from tests.conftest import register_and_verify_user


def _make_minimal_pdf() -> bytes:
    """Create a minimal valid PDF in memory for testing."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_minimal_docx() -> bytes:
    """Create a minimal valid .docx file in memory for testing."""
    import docx as docx_lib

    doc = docx_lib.Document()
    doc.add_paragraph("Hello from docx test document.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _fake_embedding_vector() -> list[float]:
    return [0.1] * 1536


def _get_unverified_user_token(db_session: Session, email: str) -> str:
    from backend.models import User

    user = User(
        email=email,
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token, _ = create_token_for_user(user)
    return token


def _create_tenant_and_token(
    http: TestClient, db: Session, *, email: str, name: str = "Tenant"
) -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(http, db, email=email)
    resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert resp.status_code == 201, resp.text
    return token, uuid.UUID(resp.json()["id"])


def _upload(http: TestClient, token: str, filename: str, content: bytes, content_type: str):
    return http.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, content_type)},
    )


def _patch_crawl_session(monkeypatch: pytest.MonkeyPatch, db_session: Session, url_service_mod) -> None:
    monkeypatch.setattr(url_service_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)


def _fake_extracted_page(url: str, title: str, text: str, *, chunk_text: str | None = None):
    chunk_text = chunk_text if chunk_text is not None else text
    return type(
        "Page",
        (),
        {
            "url": url,
            "title": title,
            "text": text,
            "chunks": [
                {
                    "chunk_text": chunk_text,
                    "chunk_index": 0,
                    "section_title": None,
                    "token_count": 1,
                    "content_hash": url,
                    "raw_text": chunk_text,
                }
            ],
        },
    )()


@pytest.mark.parametrize(
    "filename, content, content_type, expected_file_type",
    [
        pytest.param("test.pdf", None, "application/pdf", "pdf", id="pdf"),
        pytest.param(
            "test.md",
            b"# Test\n\nThis is a test document.",
            "text/markdown",
            "markdown",
            id="markdown",
        ),
        pytest.param(
            "api.json",
            b'{"openapi":"3.0.0","info":{"title":"Test API","version":"1.0"},'
            b'"paths":{"/test":{"get":{"description":"Test endpoint"}}}}',
            "application/json",
            "swagger",
            id="swagger_json",
        ),
        pytest.param(
            "api.yaml",
            b"""
openapi: 3.0.0
info:
  title: YAML Test API
  version: "1.0"
paths:
  /users:
    post:
      summary: Create user
      responses:
        "201":
          description: Created
""",
            "application/yaml",
            "swagger",
            id="swagger_yaml",
        ),
        pytest.param(
            "report.docx",
            None,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
            id="docx",
        ),
        pytest.param(
            "notes.txt",
            b"This is a plain text document.\n\nIt has multiple paragraphs.",
            "text/plain",
            "plaintext",
            id="txt",
        ),
    ],
)
def test_upload_various_file_types_succeeds(
    tenant: TestClient,
    db_session: Session,
    filename: str,
    content: bytes | None,
    content_type: str,
    expected_file_type: str,
) -> None:
    """Every supported file type must parse and report status=ready."""
    if content is None:
        content = _make_minimal_pdf() if filename.endswith(".pdf") else _make_minimal_docx()
    token, _ = _create_tenant_and_token(
        tenant, db_session, email=f"upload-{expected_file_type}@example.com"
    )
    response = _upload(tenant, token, filename, content, content_type)
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == filename
    assert data["file_type"] == expected_file_type
    assert data["status"] == "ready"


@pytest.mark.parametrize(
    "filename, content, expected_language, expected_script",
    [
        pytest.param(
            "guide.md",
            (
                b"# Onboarding Guide\n\n"
                b"This document explains how customers can reset their password and enable "
                b"two-factor authentication. The verification code is sent via email."
            ),
            "en",
            None,
            id="english",
        ),
        pytest.param(
            "guide-ru.md",
            (
                "# Инструкция\n\n"
                "Этот документ описывает, как клиенты могут сбросить пароль и включить "
                "двухфакторную аутентификацию. Код подтверждения приходит по электронной почте."
            ).encode("utf-8"),
            "ru",
            None,
            id="russian",
        ),
        pytest.param(
            "odigos.md",
            (
                "# Οδηγός\n\n"
                "Αυτό το έγγραφο εξηγεί πώς οι πελάτες μπορούν να επαναφέρουν τον κωδικό "
                "πρόσβασης και να ενεργοποιήσουν την ταυτοποίηση δύο παραγόντων."
            ).encode("utf-8"),
            None,
            "greek",
            id="greek_script",
        ),
    ],
)
def test_upload_persists_detected_language_and_script(
    tenant: TestClient,
    db_session: Session,
    filename: str,
    content: bytes,
    expected_language: str | None,
    expected_script: str | None,
) -> None:
    """Document.language/.script must be populated at parse time for cross-lingual
    retrieval and KB script detection.

    The Greek case uses a writing system the old two-bucket detector could not
    represent, so that assertion cannot pass by accident.
    """
    token, _ = _create_tenant_and_token(tenant, db_session, email=f"lang-{filename}@example.com")
    response = _upload(tenant, token, filename, content, "text/markdown")
    assert response.status_code == 201
    doc = db_session.query(Document).filter(Document.filename == filename).first()
    assert doc is not None
    if expected_language is not None:
        assert doc.language == expected_language
    if expected_script is not None:
        assert doc.script == expected_script


@pytest.mark.parametrize(
    "with_tenant, with_auth, filename, content, content_type, expected_status, detail_substring",
    [
        pytest.param(
            True, True, "virus.exe", b"MZ", "application/octet-stream", 400, "unsupported",
            id="unsupported_type",
        ),
        pytest.param(
            True, True, "huge.pdf", b"x" * (51 * 1024 * 1024), "application/pdf", 400, "too large",
            id="too_large",
        ),
        pytest.param(
            False, True, "test.md", b"# Test", "text/markdown", 404, "tenant",
            id="no_client",
        ),
        pytest.param(
            True, False, "test.md", b"# Test", "text/markdown", 401, None,
            id="unauthenticated",
        ),
    ],
)
def test_upload_rejections(
    tenant: TestClient,
    db_session: Session,
    with_tenant: bool,
    with_auth: bool,
    filename: str,
    content: bytes,
    content_type: str,
    expected_status: int,
    detail_substring: str | None,
) -> None:
    """Covers: unsupported extension, oversized file, missing tenant, missing auth."""
    token = register_and_verify_user(
        tenant, db_session, email=f"reject-{expected_status}-{filename}@example.com"
    )
    if with_tenant:
        tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Reject Tenant"},
        )
    headers = {"Authorization": f"Bearer {token}"} if with_auth else {}
    response = tenant.post(
        "/documents",
        headers=headers,
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == expected_status
    if detail_substring is not None:
        assert detail_substring in response.json()["detail"].lower()


def test_upload_document_limit_is_shared_capacity(tenant: TestClient, db_session: Session) -> None:
    """The tenant-wide document capacity should fill up and reject the next upload."""
    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="limit100@example.com", name="Limit Tenant"
    )

    for index in range(KNOWLEDGE_DOCUMENT_CAPACITY):
        db_session.add(
            Document(
                tenant_id=tenant_id,
                filename=f"existing-{index}.md",
                file_type=DocumentType.markdown,
                status=DocumentStatus.ready,
                parsed_text=f"doc {index}",
            )
        )
    db_session.commit()

    response = _upload(tenant, token, "overflow.md", b"# Overflow", "text/markdown")

    assert response.status_code == 400
    assert response.json()["detail"] == f"Document limit reached (max {KNOWLEDGE_DOCUMENT_CAPACITY})"


def test_list_knowledge_sources_requires_client(tenant: TestClient, db_session: Session) -> None:
    """Knowledge sources should match other document routes and 404 without a tenant."""
    token = register_and_verify_user(tenant, db_session, email="sources-noclient@example.com")

    response = tenant.get(
        "/documents/sources",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tenant not found"


def test_document_list_and_detail_journey(tenant: TestClient, db_session: Session) -> None:
    """Covers: empty list, list after upload, detail retrieval, cross-user isolation."""
    token_a, _ = _create_tenant_and_token(
        tenant, db_session, email="doc-journey-a@example.com", name="Journey Tenant A"
    )

    empty = tenant.get("/documents", headers={"Authorization": f"Bearer {token_a}"})
    assert empty.status_code == 200
    assert empty.json()["documents"] == []

    upload1 = _upload(
        tenant,
        token_a,
        "doc1.md",
        b"# Test\n\nThis is a test document with some content.",
        "text/markdown",
    )
    _upload(tenant, token_a, "doc2.md", b"# Doc 2", "text/markdown")

    listing = tenant.get("/documents", headers={"Authorization": f"Bearer {token_a}"})
    assert listing.status_code == 200
    listing_data = listing.json()
    assert len(listing_data["documents"]) == 2
    assert {d["filename"] for d in listing_data["documents"]} == {"doc1.md", "doc2.md"}

    doc_id = upload1.json()["id"]
    detail = tenant.get(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["id"] == str(doc_id)
    assert detail_data["filename"] == "doc1.md"
    assert "Test" in (detail_data["parsed_text"] or "")
    assert "test document" in (detail_data["parsed_text"] or "")

    token_b = register_and_verify_user(tenant, db_session, email="doc-journey-b@example.com")
    cross_user = tenant.get(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert cross_user.status_code == 404


def test_delete_document_journey(tenant: TestClient, db_session: Session) -> None:
    """Covers: wrong user cannot delete; owner delete succeeds and document is gone."""
    token_a, _ = _create_tenant_and_token(
        tenant, db_session, email="del-journey-a@example.com", name="Journey Tenant A"
    )
    upload = _upload(tenant, token_a, "protected.md", b"# Protected", "text/markdown")
    doc_id = upload.json()["id"]

    token_b = register_and_verify_user(tenant, db_session, email="del-journey-b@example.com")
    forbidden = tenant.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert forbidden.status_code == 404

    still_there = tenant.get(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert still_there.status_code == 200

    deleted = tenant.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert deleted.status_code == 204

    gone = tenant.get(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert gone.status_code == 404


def test_get_url_source_detail_includes_runs_and_quick_answers(
    tenant: TestClient, db_session: Session
) -> None:
    """Source detail must return the five newest runs (SQL-ordered) and stored quick answers."""
    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="source-detail@example.com", name="Source Detail Tenant"
    )

    source = UrlSource(
        tenant_id=tenant_id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.weekly,
        pages_indexed=1,
        chunks_created=2,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()

    base_time = datetime.now(timezone.utc)
    expected_statuses: list[str] = []
    for index in range(7):
        run = UrlSourceRun(
            source_id=source.id,
            status=f"run-{index}",
            pages_indexed=index,
            failed_urls=[],
            created_at=base_time + timedelta(minutes=index),
        )
        db_session.add(run)
        if index >= 2:
            expected_statuses.insert(0, run.status)

    db_session.add(
        QuickAnswer(
            tenant_id=tenant_id,
            source_id=source.id,
            key="documentation_url",
            value="https://docs.example.com/",
            source_url="https://docs.example.com/",
            metadata_json={"method": "source_url"},
        )
    )
    db_session.commit()

    response = tenant.get(
        f"/documents/sources/{source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    recent_runs = payload["recent_runs"]
    assert len(recent_runs) == 5
    assert [run["status"] for run in recent_runs] == expected_statuses
    assert payload["quick_answers"] == [
        {
            "key": "documentation_url",
            "value": "https://docs.example.com/",
            "source_url": "https://docs.example.com/",
            "detected_at": payload["quick_answers"][0]["detected_at"],
        }
    ]


@patch("backend.auth.routes.send_email")
def test_delete_url_source_requires_verified_user(
    mock_send_email, tenant: TestClient, db_session: Session
) -> None:
    token = _get_unverified_user_token(db_session, "unverified-source-delete@example.com")
    from backend.models import User

    user = db_session.query(User).filter(User.email == "unverified-source-delete@example.com").first()
    assert user is not None
    owner_client, _ = create_tenant(user.id, "Unverified Tenant", db_session)

    source = UrlSource(
        tenant_id=owner_client.id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.weekly,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.commit()

    response = tenant.delete(
        f"/documents/sources/{source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified."


def test_create_url_source_rejects_duplicate_normalized_domain(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, _ = _create_tenant_and_token(
        tenant, db_session, email="source-dup@example.com", name="Source Dup Tenant"
    )

    monkeypatch.setattr(
        "backend.documents.http_client._fetch_reachable_page",
        lambda url, timeout_seconds: ("<html></html>", "Docs"),
    )
    monkeypatch.setattr("backend.documents.http_client._validate_public_hostname", lambda hostname: None)
    monkeypatch.setattr("backend.documents.sitemap._load_robots_warning", lambda url: None)
    monkeypatch.setattr(
        "backend.documents.url_service._discover_urls",
        lambda root_url, exclusions, page_cap: [root_url],
    )

    first_response = tenant.post(
        "/documents/sources/url",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://docs.example.com/start", "schedule": "manual"},
    )
    second_response = tenant.post(
        "/documents/sources/url",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://docs.example.com/another", "schedule": "manual"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert "already have a source from this domain" in second_response.json()["detail"].lower()


def test_refresh_url_source_returns_429_inside_cooldown(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="source-refresh@example.com", name="Source Refresh Tenant"
    )

    source = UrlSource(
        tenant_id=tenant_id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.weekly,
        # Naive UTC — matches the project-wide ``DateTime`` column policy
        # (no ``timezone=True``). The ``before_flush`` listener would strip
        # tzinfo anyway, but writing naive directly documents the contract
        # and keeps the in-test monkeypatch below symmetric.
        last_refresh_requested_at=datetime.now(timezone.utc).replace(tzinfo=None),
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.commit()

    monkeypatch.setattr(
        "backend.documents.url_service._utcnow",
        lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    response = tenant.post(
        f"/documents/sources/{source.id}/refresh",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 429
    assert "refresh available in" in response.json()["detail"].lower()


def test_url_source_crawl_uses_remaining_shared_capacity(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL pages should consume the same shared capacity pool as uploaded files."""
    from backend.documents import url_service

    _patch_crawl_session(monkeypatch, db_session, url_service)

    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="shared-capacity@example.com", name="Shared Capacity Tenant"
    )

    prefill_count = KNOWLEDGE_DOCUMENT_CAPACITY - 40
    for index in range(prefill_count):
        db_session.add(
            Document(
                tenant_id=tenant_id,
                filename=f"file-{index}.md",
                file_type=DocumentType.markdown,
                status=DocumentStatus.ready,
                parsed_text=f"file {index}",
            )
        )

    source = UrlSource(
        tenant_id=tenant_id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.queued,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.commit()

    discovered_urls = [f"https://docs.example.com/page-{index}" for index in range(60)]
    monkeypatch.setattr(url_service, "_discover_urls", lambda *_args, **_kwargs: discovered_urls)
    monkeypatch.setattr(http_client_mod, "_fetch_page_html", lambda url: f"<html>{url}</html>")
    monkeypatch.setattr(
        embedder_mod,
        "_extract_page",
        lambda url, html: _fake_extracted_page(url, url.rsplit("/", 1)[-1], html),
    )
    monkeypatch.setattr(embedder_mod, "_embed_chunks", lambda chunks, api_key: [_fake_embedding_vector() for _ in chunks])

    url_service.crawl_url_source(source.id, api_key="test-key")
    db_session.expire_all()

    refreshed_source = db_session.query(UrlSource).filter(UrlSource.id == source.id).first()
    source_docs = db_session.query(Document).filter(Document.source_id == source.id).all()

    assert refreshed_source is not None
    assert len(source_docs) == 40
    assert refreshed_source.pages_indexed == 40
    assert refreshed_source.warning_message is not None
    assert "Knowledge capacity reached" in refreshed_source.warning_message
    assert db_session.query(Document).filter(Document.tenant_id == tenant_id).count() == KNOWLEDGE_DOCUMENT_CAPACITY


def test_url_source_refresh_updates_existing_pages_without_exceeding_shared_capacity(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh should update existing source pages and only add new pages while capacity remains."""
    from backend.documents import url_service

    _patch_crawl_session(monkeypatch, db_session, url_service)

    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="refresh-capacity@example.com", name="Refresh Capacity Tenant"
    )

    source = UrlSource(
        tenant_id=tenant_id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=60,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()

    for index in range(KNOWLEDGE_DOCUMENT_CAPACITY - 60):
        db_session.add(
            Document(
                tenant_id=tenant_id,
                filename=f"other-{index}.md",
                file_type=DocumentType.markdown,
                status=DocumentStatus.ready,
                parsed_text=f"other {index}",
            )
        )
    for index in range(60):
        db_session.add(
            Document(
                tenant_id=tenant_id,
                source_id=source.id,
                source_url=f"https://docs.example.com/page-{index}",
                filename=f"page-{index}",
                file_type=DocumentType.url,
                status=DocumentStatus.ready,
                parsed_text=f"old {index}",
            )
        )
    db_session.commit()

    discovered_urls = [f"https://docs.example.com/page-{index}" for index in range(70)]
    monkeypatch.setattr(url_service, "_discover_urls", lambda *_args, **_kwargs: discovered_urls)
    monkeypatch.setattr(http_client_mod, "_fetch_page_html", lambda url: f"<html>{url}</html>")
    monkeypatch.setattr(
        embedder_mod,
        "_extract_page",
        lambda url, html: _fake_extracted_page(url, url.rsplit("/", 1)[-1], f"updated {url}"),
    )
    monkeypatch.setattr(embedder_mod, "_embed_chunks", lambda chunks, api_key: [_fake_embedding_vector() for _ in chunks])

    url_service.crawl_url_source(source.id, api_key="test-key")
    db_session.expire_all()

    refreshed_source = db_session.query(UrlSource).filter(UrlSource.id == source.id).first()
    source_docs = db_session.query(Document).filter(Document.source_id == source.id).all()

    assert refreshed_source is not None
    assert len(source_docs) == 60
    assert refreshed_source.pages_indexed == 60
    assert refreshed_source.warning_message is not None
    assert "Knowledge capacity reached" in refreshed_source.warning_message
    assert db_session.query(Document).filter(Document.tenant_id == tenant_id).count() == KNOWLEDGE_DOCUMENT_CAPACITY


def test_delete_source_page_journey(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: successful delete persists an exclusion; deleting via the wrong
    source 404s and leaves the page intact; a later crawl does not recreate a
    manually excluded page."""
    from backend.documents import url_service

    _patch_crawl_session(monkeypatch, db_session, url_service)

    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="delete-source-page@example.com", name="Delete Source Page Tenant"
    )

    source = UrlSource(
        tenant_id=tenant_id,
        name="Docs",
        url="https://docs.example.com/start",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_found=1,
        pages_indexed=1,
        chunks_created=1,
        tokens_used=0,
        metadata_json={},
    )
    other_source = UrlSource(
        tenant_id=tenant_id,
        name="Docs Other",
        url="https://docs.example.com/other",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add_all([source, other_source])
    db_session.flush()

    doc = Document(
        tenant_id=tenant_id,
        source_id=source.id,
        filename="Getting Started",
        file_type=DocumentType.url,
        status=DocumentStatus.ready,
        parsed_text="hello",
        source_url="https://docs.example.com/start",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        Embedding(document_id=doc.id, chunk_text="hello", vector=_fake_embedding_vector(), metadata_json={})
    )
    db_session.commit()

    wrong_source_response = tenant.delete(
        f"/documents/sources/{other_source.id}/pages/{doc.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert wrong_source_response.status_code == 404
    assert db_session.query(Document).filter(Document.id == doc.id).first() is not None

    response = tenant.delete(
        f"/documents/sources/{source.id}/pages/{doc.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204
    assert db_session.query(Document).filter(Document.id == doc.id).first() is None
    assert db_session.query(Embedding).filter(Embedding.document_id == doc.id).count() == 0

    refreshed_source = db_session.query(UrlSource).filter(UrlSource.id == source.id).first()
    assert refreshed_source is not None
    assert refreshed_source.pages_indexed == 0
    assert refreshed_source.chunks_created == 0
    assert refreshed_source.metadata_json["manually_excluded_page_urls"] == ["https://docs.example.com/start"]

    monkeypatch.setattr(
        url_service, "_discover_urls", lambda root_url, exclusions, page_cap: ["https://docs.example.com/start"]
    )
    monkeypatch.setattr(http_client_mod, "_fetch_page_html", lambda url: "<html><body>start</body></html>")
    monkeypatch.setattr(
        embedder_mod, "_extract_page", lambda url, html: _fake_extracted_page(url, "Start", "start")
    )
    monkeypatch.setattr(embedder_mod, "_embed_chunks", lambda chunks, api_key: [_fake_embedding_vector() for _ in chunks])

    url_service.crawl_url_source(source.id, api_key="test-key")
    db_session.expire_all()

    recrawled_source = db_session.query(UrlSource).filter(UrlSource.id == source.id).first()
    source_docs = db_session.query(Document).filter(Document.source_id == source.id).all()

    assert recrawled_source is not None
    assert recrawled_source.pages_indexed == 0
    assert recrawled_source.pages_found == 0
    assert source_docs == []


def test_crawl_url_source_detects_openapi_yaml_and_indexes_as_swagger(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.documents import url_service

    _patch_crawl_session(monkeypatch, db_session, url_service)

    token, tenant_id = _create_tenant_and_token(
        tenant, db_session, email="openapi-url@example.com", name="OpenAPI URL Tenant"
    )

    source = UrlSource(
        tenant_id=tenant_id,
        name="API spec",
        url="https://docs.example.com/openapi.yaml",
        normalized_domain="docs.example.com",
        status=SourceStatus.queued,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.commit()

    monkeypatch.setattr(url_service, "_discover_urls", lambda *_args, **_kwargs: [source.url])
    monkeypatch.setattr(http_client_mod, "_validate_public_hostname", lambda hostname: None)

    yaml_spec = """
openapi: 3.0.0
info:
  title: URL API
  version: "1.0"
paths:
  /users:
    get:
      summary: List users
      operationId: listUsers
      responses:
        "200":
          description: OK
  /users/{userId}:
    get:
      summary: Get user
      parameters:
        - in: path
          name: userId
          required: true
          schema:
            type: string
      responses:
        "200":
          description: OK
"""

    monkeypatch.setattr(
        http_client_mod,
        "_http_client",
        lambda timeout_seconds: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/yaml"},
                    text=yaml_spec,
                    request=request,
                )
            ),
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    monkeypatch.setattr(embedder_mod, "_embed_chunks", lambda chunks, api_key: [_fake_embedding_vector() for _ in chunks])

    url_service.crawl_url_source(source.id, api_key="test-key")
    db_session.expire_all()

    refreshed_source = db_session.query(UrlSource).filter(UrlSource.id == source.id).first()
    doc = db_session.query(Document).filter(Document.source_id == source.id).first()
    embeddings = (
        db_session.query(Embedding)
        .filter(Embedding.document_id == doc.id)
        .order_by(Embedding.created_at.asc())
        .all()
    )

    assert refreshed_source is not None
    assert refreshed_source.status == SourceStatus.ready
    assert refreshed_source.metadata_json["platform"] == "openapi"
    assert doc is not None
    assert doc.file_type == DocumentType.swagger
    assert "Endpoint: GET /users" in (doc.parsed_text or "")
    assert len(embeddings) == 2
    assert embeddings[0].metadata_json["type"] == "api_endpoint"
    assert embeddings[0].metadata_json["source_kind"] == "url"
    assert embeddings[0].metadata_json["path"] == "/users"
