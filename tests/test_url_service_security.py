from __future__ import annotations

import socket
import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.clients.service import create_client
from backend.core.security import hash_password
from backend.documents import url_service
from backend.documents.parsers import build_openapi_ingestion_payload
from backend.documents.schemas import (
    SOURCE_TYPE_URL,
    UrlSourceCreateRequest,
    UrlSourceRunResponse,
    UrlSourceUpdateRequest,
)
from backend.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    SourceSchedule,
    SourceStatus,
    User,
    UrlSource,
)


def test_validate_public_hostname_rejects_private_ip() -> None:
    with pytest.raises(HTTPException) as exc_info:
        url_service._validate_public_hostname("127.0.0.1")

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail).lower()


def test_validate_public_hostname_rejects_private_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, port: int | None, type: int = 0):  # type: ignore[override]
        assert host == "internal.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(url_service.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc_info:
        url_service._validate_public_hostname("internal.example.com")

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail).lower()


def test_request_with_safe_redirects_blocks_local_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: int | None, type: int = 0):  # type: ignore[override]
        if host == "docs.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        if host == "localhost":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        raise socket.gaierror(host)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://docs.example.com/start"
        return httpx.Response(
            302,
            headers={"location": "http://localhost:8080/admin"},
            request=request,
        )

    monkeypatch.setattr(url_service.socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        with pytest.raises(HTTPException) as exc_info:
            url_service._request_with_safe_redirects(
                client,
                "GET",
                "https://docs.example.com/start",
                context=url_service.FetchContext(stage="test", url="https://docs.example.com/start"),
            )

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail).lower()


def test_fetch_reachable_page_returns_404_for_missing_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: int | None, type: int = 0):  # type: ignore[override]
        assert host == "docs.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    monkeypatch.setattr(url_service.socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    original_client_factory = url_service._http_client
    monkeypatch.setattr(
        url_service,
        "_http_client",
        lambda timeout_seconds: httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            url_service._fetch_reachable_page("https://docs.example.com/missing", 5.0)
    finally:
        monkeypatch.setattr(url_service, "_http_client", original_client_factory)

    assert exc_info.value.status_code == 404
    assert "404" in str(exc_info.value.detail)


def test_url_source_create_request_validates_http_url_and_schedule() -> None:
    payload = UrlSourceCreateRequest(url="https://docs.example.com", schedule="manual")

    assert str(payload.url) == "https://docs.example.com/"
    assert payload.schedule == "manual"

    with pytest.raises(Exception):
        UrlSourceCreateRequest(url="ftp://docs.example.com", schedule="weekly")

    with pytest.raises(Exception):
        UrlSourceCreateRequest(url="https://docs.example.com", schedule="monthly")


def test_url_source_update_request_accepts_only_supported_schedules() -> None:
    payload = UrlSourceUpdateRequest(schedule="daily")
    assert payload.schedule == "daily"

    with pytest.raises(Exception):
        UrlSourceUpdateRequest(schedule="monthly")


def test_url_source_request_rejects_too_many_exclusions() -> None:
    exclusions = [f"/docs/{index}" for index in range(51)]

    with pytest.raises(Exception):
        UrlSourceCreateRequest(url="https://docs.example.com", exclusions=exclusions)

    with pytest.raises(Exception):
        UrlSourceUpdateRequest(exclusions=exclusions)


def test_url_source_request_rejects_too_long_exclusion() -> None:
    too_long = "/" + ("a" * 255)

    with pytest.raises(Exception):
        UrlSourceCreateRequest(url="https://docs.example.com", exclusions=[too_long])

    with pytest.raises(Exception):
        UrlSourceUpdateRequest(exclusions=[too_long])


def test_url_source_run_response_uses_typed_failed_urls() -> None:
    payload = UrlSourceRunResponse(
        id=uuid.uuid4(),
        status="error",
        pages_indexed=0,
        failed_urls=[{"url": "https://docs.example.com/a", "reason": "timeout"}],
        created_at="2026-03-25T10:00:00Z",
    )

    assert payload.failed_urls[0].url == "https://docs.example.com/a"
    assert payload.failed_urls[0].reason == "timeout"


def test_discover_urls_respects_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    adjacency = {
        "https://docs.example.com/": [
            "https://docs.example.com/a",
            "https://docs.example.com/b",
        ],
        "https://docs.example.com/a": ["https://docs.example.com/c"],
        "https://docs.example.com/b": ["https://docs.example.com/d"],
        "https://docs.example.com/c": [],
        "https://docs.example.com/d": [],
    }

    monkeypatch.setattr(
        url_service,
        "_normalize_source_url",
        lambda root_url: ("https://docs.example.com/", "docs.example.com"),
    )
    monkeypatch.setattr(url_service, "_fetch_sitemap_urls", lambda root_url, domain: [])
    monkeypatch.setattr(url_service, "_is_html_like", lambda response: True)
    monkeypatch.setattr(
        url_service,
        "_extract_links",
        lambda html, current_url, domain: adjacency.get(current_url, []),
    )

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(url_service, "_http_client", lambda timeout_seconds: DummyClient())
    monkeypatch.setattr(
        url_service,
        "_request_with_safe_redirects",
        lambda client, method, url, context: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html></html>",
        ),
    )

    urls = url_service._discover_urls("https://docs.example.com", [], page_cap=3)

    assert urls == [
        "https://docs.example.com/",
        "https://docs.example.com/a",
        "https://docs.example.com/b",
    ]


def test_upsert_page_document_skips_reembedding_when_hash_matches(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(
        email="hash-check@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    client = create_client(user.id, "Client", db_session)

    source = UrlSource(
        client_id=client.id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()

    document = Document(
        client_id=client.id,
        source_id=source.id,
        filename="Existing",
        file_type=DocumentType.url,
        status=DocumentStatus.ready,
        source_url="https://docs.example.com/page",
        parsed_text="Same content",
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        Embedding(
            document_id=document.id,
            chunk_text="chunk",
            vector=[0.1] * 1536,
            metadata_json={},
        )
    )
    db_session.commit()

    page = url_service.ExtractedPage(
        url="https://docs.example.com/page",
        title="Updated title",
        text="Same content",
        chunks=[],
    )

    def fail_embed(*args, **kwargs):
        raise AssertionError("_embed_chunks should not be called for unchanged content")

    monkeypatch.setattr(url_service, "_embed_chunks", fail_embed)

    updated_doc, chunk_count = url_service._upsert_page_document(
        source=source,
        page=page,
        db=db_session,
        api_key="sk-test",
    )

    assert updated_doc.id == document.id
    assert updated_doc.filename == "Updated title"
    assert chunk_count == 1


def test_crawl_url_source_marks_run_error_when_failures_exceed_threshold(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(bind=engine)
    user = User(
        email="fail-check@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=True,
    )
    session.add(user)
    session.flush()
    client = create_client(user.id, "Client", session)

    source = UrlSource(
        client_id=client.id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.queued,
        crawl_schedule=SourceSchedule.weekly,
        metadata_json={},
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    monkeypatch.setattr(url_service, "SessionLocal", lambda: Session(bind=engine))
    monkeypatch.setattr(
        url_service,
        "_discover_urls",
        lambda root_url, exclusions, page_cap: [
            "https://docs.example.com/a",
            "https://docs.example.com/b",
            "https://docs.example.com/c",
        ],
    )

    call_count = {"count": 0}

    def fake_fetch_page_html(url: str) -> str | None:
        call_count["count"] += 1
        if call_count["count"] == 1:
            return "<html><body>root</body></html>"
        return None

    monkeypatch.setattr(url_service, "_fetch_page_html", fake_fetch_page_html)

    url_service.crawl_url_source(source_id, "sk-test")

    verify_session = Session(bind=engine)
    refreshed_source = verify_session.query(UrlSource).filter(UrlSource.id == source_id).first()
    run = verify_session.query(url_service.UrlSourceRun).filter_by(source_id=source_id).first()

    assert refreshed_source is not None
    assert refreshed_source.status == SourceStatus.error
    assert refreshed_source.error_message == "Indexing failed — most pages were unreachable."
    assert run is not None
    assert run.status == SourceStatus.error.value
    assert len(run.failed_urls) == 3
    verify_session.close()


def test_url_source_response_constant_exposed() -> None:
    assert SOURCE_TYPE_URL == "url"


def test_upsert_structured_document_skips_reembedding_when_hash_matches(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(
        email="structured-hash@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    client = create_client(user.id, "Client", db_session)

    source = UrlSource(
        client_id=client.id,
        name="API Docs",
        url="https://docs.example.com/openapi.yaml",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()

    parsed_text, openapi_chunks, _, _ = build_openapi_ingestion_payload(
        b"""
openapi: 3.0.0
info:
  title: Existing API
  version: "1.0"
paths:
  /users:
    get:
      summary: List users
      responses:
        "200":
          description: OK
"""
    )

    document = Document(
        client_id=client.id,
        source_id=source.id,
        filename="Existing API",
        file_type=DocumentType.swagger,
        status=DocumentStatus.ready,
        source_url="https://docs.example.com/openapi.yaml",
        parsed_text=parsed_text,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        Embedding(
            document_id=document.id,
            chunk_text="chunk",
            vector=[0.1] * 1536,
            metadata_json={},
        )
    )
    db_session.commit()

    def fail_embed(*args, **kwargs):
        raise AssertionError("_embed_chunks should not be called for unchanged structured content")

    monkeypatch.setattr(url_service, "_embed_chunks", fail_embed)

    updated_doc, chunk_count = url_service._upsert_structured_document(
        source=source,
        url="https://docs.example.com/openapi.yaml",
        title="Updated API",
        parsed_text=parsed_text,
        chunks=openapi_chunks,
        db=db_session,
        api_key="sk-test",
    )

    assert updated_doc.id == document.id
    assert updated_doc.filename == "Updated API"
    assert updated_doc.file_type == DocumentType.swagger
    assert chunk_count == 1


def test_fetch_openapi_source_rejects_structured_non_openapi_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(url_service, "_validate_public_hostname", lambda hostname: None)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"paths": "not-an-object", "hello": "world"},
            request=request,
        )
    )
    monkeypatch.setattr(
        url_service,
        "_http_client",
        lambda timeout_seconds: httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        url_service._fetch_openapi_source("https://docs.example.com/openapi.json")

    assert exc_info.value.status_code == 422
    assert "could not be validated" in str(exc_info.value.detail).lower()


def test_crawl_url_source_marks_error_for_invalid_structured_openapi_payload(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(bind=engine)
    user = User(
        email="invalid-structured@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=True,
    )
    session.add(user)
    session.flush()
    client = create_client(user.id, "Client", session)

    source = UrlSource(
        client_id=client.id,
        name="API Docs",
        url="https://docs.example.com/openapi.json",
        normalized_domain="docs.example.com",
        status=SourceStatus.queued,
        crawl_schedule=SourceSchedule.manual,
        metadata_json={},
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    monkeypatch.setattr(url_service, "SessionLocal", lambda: Session(bind=engine))
    monkeypatch.setattr(url_service, "_validate_public_hostname", lambda hostname: None)
    monkeypatch.setattr(url_service, "_discover_urls", lambda *_args, **_kwargs: [source.url])
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"paths": "not-an-object", "hello": "world"},
            request=request,
        )
    )
    monkeypatch.setattr(
        url_service,
        "_http_client",
        lambda timeout_seconds: httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ),
    )

    url_service.crawl_url_source(source_id, "sk-test")

    verify_session = Session(bind=engine)
    refreshed_source = verify_session.query(UrlSource).filter(UrlSource.id == source_id).first()
    run = verify_session.query(url_service.UrlSourceRun).filter_by(source_id=source_id).first()

    assert refreshed_source is not None
    assert refreshed_source.status == SourceStatus.error
    assert "could not be validated" in str(refreshed_source.error_message).lower()
    assert run is not None
    assert run.status == SourceStatus.error.value
    verify_session.close()
