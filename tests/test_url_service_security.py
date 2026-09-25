from __future__ import annotations

import socket
import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.scripts import detect_script_bucket
from backend.core.security import hash_password
from backend.tenants.service import create_tenant
from backend.documents import embedder as embedder_mod
from backend.documents import http_client as http_client_mod
from backend.documents import sitemap as sitemap_mod
from backend.documents import url_service
from backend.documents.parsers import build_openapi_ingestion_payload
from backend.documents.schemas import (
    UrlSourceCreateRequest,
    UrlSourceRunResponse,
    UrlSourceUpdateRequest,
)
from backend.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    QuickAnswer,
    SourceSchedule,
    SourceStatus,
    User,
    UrlSource,
)


def _make_tenant(db: Session, email: str, name: str = "Tenant"):
    user = User(email=email, password_hash=hash_password("SecurePass1!"), is_verified=True)
    db.add(user)
    db.flush()
    return create_tenant(user.id, name, db)


def test_scan_html_for_quick_answers_detects_expected_fields() -> None:
    html = """
    <html>
      <body>
        <footer>
          <a href="mailto:help@example.com">Email support</a>
          <a href="/docs">Documentation</a>
          <a href="/pricing">Pricing</a>
          <a href="https://status.example.com">Status page</a>
        </footer>
        <p>Start your free trial for 14 days today.</p>
        <script src="https://widget.intercom.io/widget/abc123.js"></script>
      </body>
    </html>
    """

    answers = url_service.scan_html_for_quick_answers(
        html=html,
        page_url="https://docs.example.com/start",
        root_url="https://docs.example.com/",
    )

    assert answers["support_email"].value == "help@example.com"
    assert answers["documentation_url"].value == "https://docs.example.com/docs"
    assert answers["pricing_url"].value == "https://docs.example.com/pricing"
    assert answers["status_page_url"].value == "https://status.example.com/"
    assert "free trial" in answers["trial_info"].value.lower()
    assert answers["support_chat"].value == "Intercom"


def test_scan_html_for_quick_answers_prefers_support_mailto_when_multiple_exist() -> None:
    html = """
    <html>
      <body>
        <footer>
          <a href="mailto:sales@example.com">Talk to sales</a>
          <a href="mailto:help@example.com">Email support</a>
        </footer>
      </body>
    </html>
    """

    answers = url_service.scan_html_for_quick_answers(
        html=html,
        page_url="https://docs.example.com/contact",
        root_url="https://docs.example.com/",
    )

    assert answers["support_email"].value == "help@example.com"


@pytest.mark.parametrize(
    "hostname, fake_getaddrinfo",
    [
        pytest.param("127.0.0.1", None, id="private_ip_literal"),
        pytest.param(
            "internal.example.com",
            lambda host, port, type=0: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))
            ],
            id="private_dns_resolution",
        ),
    ],
)
def test_validate_public_hostname_rejects_private_targets(
    hostname: str, fake_getaddrinfo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSRF guard: reject a literal private IP and a hostname that resolves to one."""
    if fake_getaddrinfo is not None:
        monkeypatch.setattr(http_client_mod.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc_info:
        http_client_mod._validate_public_hostname(hostname)

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

    monkeypatch.setattr(http_client_mod.socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as tenant:
        with pytest.raises(HTTPException) as exc_info:
            http_client_mod._request_with_safe_redirects(
                tenant,
                "GET",
                "https://docs.example.com/start",
                context=http_client_mod.FetchContext(stage="test", url="https://docs.example.com/start"),
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

    monkeypatch.setattr(http_client_mod.socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    monkeypatch.setattr(
        http_client_mod,
        "_http_client",
        lambda timeout_seconds: httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        http_client_mod._fetch_reachable_page("https://docs.example.com/missing", 5.0)

    assert exc_info.value.status_code == 404
    assert "404" in str(exc_info.value.detail)


def test_url_source_create_request_parses_valid_url_and_schedule() -> None:
    payload = UrlSourceCreateRequest(url="https://docs.example.com", schedule="manual")

    assert str(payload.url) == "https://docs.example.com/"
    assert payload.schedule == "manual"


@pytest.mark.parametrize(
    "model_cls, kwargs",
    [
        pytest.param(UrlSourceCreateRequest, {"url": "ftp://docs.example.com"}, id="non_http_scheme"),
        pytest.param(
            UrlSourceCreateRequest,
            {"url": "https://docs.example.com", "schedule": "monthly"},
            id="unsupported_schedule_create",
        ),
        pytest.param(UrlSourceUpdateRequest, {"schedule": "monthly"}, id="unsupported_schedule_update"),
        pytest.param(
            UrlSourceCreateRequest,
            {"url": "https://docs.example.com", "exclusions": [f"/docs/{i}" for i in range(51)]},
            id="too_many_exclusions_create",
        ),
        pytest.param(
            UrlSourceUpdateRequest,
            {"exclusions": [f"/docs/{i}" for i in range(51)]},
            id="too_many_exclusions_update",
        ),
        pytest.param(
            UrlSourceCreateRequest,
            {"url": "https://docs.example.com", "exclusions": ["/" + ("a" * 255)]},
            id="too_long_exclusion_create",
        ),
        pytest.param(
            UrlSourceUpdateRequest,
            {"exclusions": ["/" + ("a" * 255)]},
            id="too_long_exclusion_update",
        ),
    ],
)
def test_url_source_request_rejects_invalid_input(model_cls, kwargs: dict) -> None:
    """Covers: non-HTTP scheme, unsupported schedule, too many/too long exclusions."""
    with pytest.raises(Exception):
        model_cls(**kwargs)


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
    monkeypatch.setattr(sitemap_mod, "_fetch_sitemap_urls", lambda root_url, domain: [])
    monkeypatch.setattr(http_client_mod, "_is_html_like", lambda response: True)
    monkeypatch.setattr(
        sitemap_mod,
        "_extract_links",
        lambda html, current_url, domain: adjacency.get(current_url, []),
    )

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(http_client_mod, "_http_client", lambda timeout_seconds: DummyClient())
    monkeypatch.setattr(
        http_client_mod,
        "_request_with_safe_redirects",
        lambda tenant, method, url, context: httpx.Response(
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


def test_fetch_sitemap_urls_expands_sitemapindex(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "https://docs.example.com/sitemap.xml": """<?xml version="1.0" encoding="utf-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://docs.example.com/sitemap-pages.xml</loc>
  </sitemap>
</sitemapindex>
""",
        "https://docs.example.com/sitemap_index.xml": """<?xml version="1.0" encoding="utf-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://docs.example.com/sitemap-pages.xml</loc>
  </sitemap>
</sitemapindex>
""",
        "https://docs.example.com/sitemap-pages.xml": """<?xml version="1.0" encoding="utf-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/guide</loc></url>
  <url><loc>https://docs.example.com/reference</loc></url>
</urlset>
""",
    }

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_request(client, method: str, url: str, context):
        assert method == "GET"
        return httpx.Response(
            200,
            headers={"content-type": "application/xml"},
            text=responses[url],
        )

    monkeypatch.setattr(sitemap_mod, "_http_client", lambda timeout_seconds: DummyClient())
    monkeypatch.setattr(sitemap_mod, "_request_with_safe_redirects", fake_request)

    urls = sitemap_mod._fetch_sitemap_urls("https://docs.example.com/", "docs.example.com")

    assert urls == [
        "https://docs.example.com/guide",
        "https://docs.example.com/reference",
    ]


def test_fetch_sitemap_urls_limits_recursive_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []
    chain_length = sitemap_mod.MAX_SITEMAPS_PER_SOURCE + 5

    def sitemap_index(next_url: str) -> str:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>{next_url}</loc>
  </sitemap>
</sitemapindex>
"""

    responses = {
        "https://docs.example.com/sitemap.xml": sitemap_index("https://docs.example.com/sitemap-chain-0.xml"),
        "https://docs.example.com/sitemap_index.xml": sitemap_index("https://docs.example.com/sitemap-chain-0.xml"),
    }
    for index in range(chain_length):
        current = f"https://docs.example.com/sitemap-chain-{index}.xml"
        next_url = f"https://docs.example.com/sitemap-chain-{index + 1}.xml"
        responses[current] = sitemap_index(next_url)

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_request(client, method: str, url: str, context):
        requested_urls.append(url)
        return httpx.Response(
            200,
            headers={"content-type": "application/xml"},
            text=responses[url],
        )

    monkeypatch.setattr(sitemap_mod, "_http_client", lambda timeout_seconds: DummyClient())
    monkeypatch.setattr(sitemap_mod, "_request_with_safe_redirects", fake_request)

    urls = sitemap_mod._fetch_sitemap_urls("https://docs.example.com/", "docs.example.com")

    assert urls == []
    assert len(requested_urls) == sitemap_mod.MAX_SITEMAPS_PER_SOURCE
    assert "https://docs.example.com/sitemap-chain-19.xml" not in requested_urls


def test_fetch_page_html_accepts_markdown_response(monkeypatch: pytest.MonkeyPatch) -> None:
    markdown = "# Docs\n\nThis page is served as markdown."

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_request(client, method: str, url: str, context):
        assert method == "GET"
        return httpx.Response(
            200,
            headers={"content-type": "text/markdown; charset=utf-8"},
            text=markdown,
        )

    monkeypatch.setattr(http_client_mod, "_http_client", lambda timeout_seconds: DummyClient())
    monkeypatch.setattr(http_client_mod, "_request_with_safe_redirects", fake_request)

    assert http_client_mod._fetch_page_html("https://docs.example.com/guide") == markdown


@pytest.mark.parametrize(
    "reasons, expected_message",
    [
        pytest.param(
            ["Could not fetch HTML", "Could not fetch HTML", "No readable content extracted"],
            "Indexing failed — most pages could not be fetched or returned an unsupported format.",
            id="fetch_and_format_dominant",
        ),
        pytest.param(
            ["No readable content extracted", "No readable content extracted", "Could not fetch HTML"],
            "Indexing failed — most pages did not contain readable content.",
            id="readable_content_dominant",
        ),
    ],
)
def test_summarize_crawl_failure_prioritizes_dominant_reason(
    reasons: list[str], expected_message: str
) -> None:
    failures = [
        {"url": f"https://docs.example.com/{i}", "reason": reason} for i, reason in enumerate(reasons)
    ]
    assert url_service._summarize_crawl_failure(failures) == expected_message


def test_upsert_page_document_skips_reembedding_when_hash_matches(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _make_tenant(db_session, "hash-check@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
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
        tenant_id=tenant.id,
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

    monkeypatch.setattr(embedder_mod, "_embed_chunks", fail_embed)

    updated_doc, chunk_count = url_service._upsert_page_document(
        source=source,
        page=page,
        db=db_session,
        api_key="sk-test",
    )

    assert updated_doc.id == document.id
    assert updated_doc.filename == "Updated title"
    assert chunk_count == 1


def test_upsert_page_document_persists_detected_script(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Document.script must be written on the crawl path too, not just uploads.

    Uses a writing system the old two-bucket detector could not represent, so
    the assertion cannot pass by accident. Also covers crawled pages running
    entity extraction (Step 4), same as uploads.
    """
    tenant = _make_tenant(db_session, "page-script@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()
    db_session.commit()

    page = url_service.ExtractedPage(
        url="https://docs.example.com/odigos",
        title="Οδηγός",
        text=(
            "Αυτό το έγγραφο εξηγεί πώς οι πελάτες μπορούν να επαναφέρουν "
            "τον κωδικό πρόσβασης από τη σελίδα του λογαριασμού."
        ),
        chunks=[
            {
                "chunk_index": 0,
                "raw_text": "Οδηγός επαναφοράς κωδικού",
                "section_title": "Οδηγός",
                "chunk_text": "Οδηγός επαναφοράς κωδικού",
                "token_count": 4,
                "content_hash": "hash",
            }
        ],
    )
    monkeypatch.setattr(embedder_mod, "_embed_chunks", lambda *a, **k: [[0.1] * 1536])
    extract_calls: list[str] = []

    def _fake_extract(text: str, api_key: str, *, tenant_id: str | None = None) -> list[str]:
        extract_calls.append(text)
        return ["Acme CRM"]

    monkeypatch.setattr(embedder_mod, "extract_entities_from_passage", _fake_extract)

    doc, _ = url_service._upsert_page_document(
        source=source,
        page=page,
        db=db_session,
        api_key="sk-test",
    )

    assert doc.script == "greek"
    rows = (
        db_session.query(Embedding)
        .filter(Embedding.document_id == doc.id)
        .order_by(Embedding.created_at.asc())
        .all()
    )
    assert len(rows) == 1
    assert len(extract_calls) == len(rows)
    for row in rows:
        assert row.entities == ["Acme CRM"]


def test_upsert_structured_document_persists_detected_script(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Document.script must be written on the structured-source path too."""
    tenant = _make_tenant(db_session, "structured-script@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
        name="API Docs",
        url="https://docs.example.com/openapi.yaml",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()
    db_session.commit()

    spec = """
openapi: 3.0.0
info:
  title: Οδηγός API
  version: "1.0"
paths:
  /users:
    get:
      summary: Κατάλογος χρηστών
      responses:
        "200":
          description: Εντάξει
"""
    parsed_text, openapi_chunks, _, _ = build_openapi_ingestion_payload(
        spec.encode("utf-8")
    )
    monkeypatch.setattr(
        embedder_mod, "_embed_chunks", lambda *a, **k: [[0.1] * 1536] * len(openapi_chunks)
    )

    doc, _ = url_service._upsert_structured_document(
        source=source,
        url="https://docs.example.com/openapi.yaml",
        title="Οδηγός API",
        parsed_text=parsed_text,
        chunks=openapi_chunks,
        db=db_session,
        api_key="sk-test",
    )

    # An OpenAPI document's parsed form is mostly schema keywords, so assert
    # the stored value against the text itself rather than a guessed script.
    assert doc.script is not None
    assert doc.script == detect_script_bucket(doc.parsed_text)


def test_upsert_page_document_runs_extraction_when_unchanged_if_env_set(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "url_knowledge_extract_when_unchanged", True)
    calls: list[dict] = []

    def capture_extraction(**kwargs):
        calls.append(dict(kwargs))

    monkeypatch.setattr(embedder_mod, "_run_tenant_knowledge_extraction_best_effort", capture_extraction)

    tenant = _make_tenant(db_session, "hash-extract-env@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
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
        tenant_id=tenant.id,
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

    monkeypatch.setattr(embedder_mod, "_embed_chunks", fail_embed)

    updated_doc, chunk_count = url_service._upsert_page_document(
        source=source,
        page=page,
        db=db_session,
        api_key="sk-test",
    )

    assert updated_doc.id == document.id
    assert chunk_count == 1
    assert len(calls) == 1
    assert calls[0]["document_id"] == document.id
    assert calls[0]["api_key"] == "sk-test"


def test_crawl_url_source_marks_run_error_when_failures_exceed_threshold(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(bind=engine)
    tenant = _make_tenant(session, "fail-check@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
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

    monkeypatch.setattr(http_client_mod, "_fetch_page_html", lambda url: None)

    url_service.crawl_url_source(source_id, "sk-test")

    with Session(bind=engine) as verify_session:
        refreshed_source = verify_session.query(UrlSource).filter(UrlSource.id == source_id).first()
        run = verify_session.query(url_service.UrlSourceRun).filter_by(source_id=source_id).first()

        assert refreshed_source is not None
        assert refreshed_source.status == SourceStatus.error
        assert (
            refreshed_source.error_message
            == "Indexing failed — most pages could not be fetched or returned an unsupported format."
        )
        assert run is not None
        assert run.status == SourceStatus.error.value
        assert len(run.failed_urls) == 3


def test_crawl_url_source_persists_quick_answers(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = _make_tenant(db_session, "quickanswers@example.com", "Quick Answers Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
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

    monkeypatch.setattr(
        url_service,
        "_plan_crawl",
        lambda source, db: url_service._CrawlPlan(
            urls=["https://docs.example.com/"],
            discovered_urls=["https://docs.example.com/"],
            remaining_capacity=100,
        ),
    )
    monkeypatch.setattr(
        url_service,
        "_fetch_openapi_source",
        lambda url: None,
    )
    monkeypatch.setattr(
        http_client_mod,
        "_fetch_page_html",
        lambda url: """
        <html>
          <body>
            <main><h1>Docs</h1><p>Use our free trial for 14 days.</p></main>
            <a href=\"mailto:help@example.com\">Support</a>
            <a href=\"/pricing\">Pricing</a>
            <a href=\"https://status.example.com\">Status</a>
          </body>
        </html>
        """,
    )
    monkeypatch.setattr(embedder_mod, "_embed_chunks", lambda chunks, api_key: [[0.1] * 1536 for _ in chunks])
    monkeypatch.setattr(
        embedder_mod,
        "_run_tenant_knowledge_extraction_best_effort",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        url_service,
        "run_mode_a_for_tenant_when_queue_empty_best_effort",
        lambda tenant_id: None,
    )
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(url_service, "SessionLocal", lambda: db_session)

    url_service.crawl_url_source(source.id, "sk-test")

    stored = (
        db_session.query(QuickAnswer)
        .filter(QuickAnswer.source_id == source.id)
        .order_by(QuickAnswer.key.asc())
        .all()
    )
    by_key = {item.key: item.value for item in stored}
    assert by_key["documentation_url"] == "https://docs.example.com/"
    assert by_key["support_email"] == "help@example.com"
    assert by_key["pricing_url"] == "https://docs.example.com/pricing"
    assert by_key["status_page_url"] == "https://status.example.com/"
    assert "free trial" in by_key["trial_info"].lower()


def test_upsert_structured_document_skips_reembedding_when_hash_matches(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _make_tenant(db_session, "structured-hash@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
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
        tenant_id=tenant.id,
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

    monkeypatch.setattr(embedder_mod, "_embed_chunks", fail_embed)

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
    monkeypatch.setattr(http_client_mod, "_validate_public_hostname", lambda hostname: None)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"paths": "not-an-object", "hello": "world"},
            request=request,
        )
    )
    monkeypatch.setattr(
        http_client_mod,
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
    tenant = _make_tenant(session, "invalid-structured@example.com", "Tenant")

    source = UrlSource(
        tenant_id=tenant.id,
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
    monkeypatch.setattr(http_client_mod, "_validate_public_hostname", lambda hostname: None)
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
        http_client_mod,
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
