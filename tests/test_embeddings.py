"""Tests for embedding creation and management API."""

from __future__ import annotations

import json
import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.documents.parsers import (
    OPENAPI_CHUNK_META_PREFIX,
    OPENAPI_OPERATION_START_MARKER,
    build_openapi_ingestion_payload,
    extract_openapi_chunks_from_rendered_text,
)
from backend.embeddings.service import _build_swagger_chunks, chunk_text
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
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Mock OpenAI, create embeddings for ready doc → chunks saved."""
    token = register_and_verify_user(tenant, db_session, email="emb@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Emb Tenant"},
    )
    set_client_openai_key(tenant, token)
    md_content = b"# Test\n\n" + b"Lorem ipsum. " * 50  # enough for multiple chunks
    upload_resp = tenant.post(
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

    response = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["status"] == "embedding"

    # Background task ran synchronously; verify chunks were created via the list endpoint
    list_resp = tenant.get(
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
    tenant: TestClient, db_session: Session
) -> None:
    """404 if doc doesn't exist."""
    token = register_and_verify_user(tenant, db_session, email="nf@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "NF Tenant"},
    )
    set_client_openai_key(tenant, token)
    fake_id = str(uuid.uuid4())
    response = tenant.post(
        f"/embeddings/documents/{fake_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_swagger_uses_endpoint_metadata(
    mock_get_openai: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="swagger-emb@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Swagger Emb Tenant"},
    )
    set_client_openai_key(tenant, token)
    swagger_content = b"""
openapi: 3.0.0
info:
  title: Team API
  version: "1.0"
paths:
  /users:
    post:
      summary: Create a user
      operationId: createUser
      tags: [users]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                email:
                  type: string
      responses:
        "201":
          description: Created
"""
    upload_resp = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("team-api.yaml", swagger_content, "application/yaml")},
    )
    doc_id = upload_resp.json()["id"]

    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(data=[Mock(embedding=[0.1] * 1536)])
    mock_get_openai.return_value = mock_client

    response = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202

    from backend.core.db import SessionLocal

    with SessionLocal() as fresh_db:
        row = (
            fresh_db.query(Embedding)
            .filter(Embedding.document_id == uuid.UUID(doc_id))
            .first()
        )

    assert row is not None
    metadata = row.metadata_json
    assert metadata["type"] == "api_endpoint"
    assert metadata["path"] == "/users"
    assert metadata["method"] == "post"
    assert metadata["operation_id"] == "createUser"
    assert metadata["source_format"] == "yaml"
    assert metadata["spec_version"] == "3.0.0"


def test_build_swagger_chunks_forces_schema_detail_split_for_rich_operation() -> None:
    swagger_content = b"""
openapi: 3.0.3
info:
  title: Rich API
  version: "1.0"
paths:
  /resources:
    post:
      summary: Create resource
      operationId: createResource
      requestBody:
        required: true
        content:
          application/json:
            schema:
              allOf:
                - type: object
                  properties:
                    id:
                      type: string
                    origin:
                      type: object
                      properties:
                        hostname:
                          type: string
                        https:
                          type: boolean
                    name:
                      type: string
                    cache:
                      type: object
                      properties:
                        disable:
                          type: boolean
                        use_stale:
                          type: boolean
                    certificate:
                      type: object
                    active:
                      type: boolean
                  required:
                    - origin
                    - name
            example:
              id: abc
              origin: https://origin.example.com
              name: example
      responses:
        "200":
          allOf:
            - description: Created
            - content:
                application/json:
                  schema:
                    type: object
                    properties:
                      status:
                        type: string
                      task_id:
                        type: string
                      resource_id:
                        type: string
                      cdn_domain:
                        type: string
                  example:
                    status: accept
                    task_id: "1"
                    resource_id: rid
                    cdn_domain: cdn.example.com
"""
    parsed_text, _, _, _ = build_openapi_ingestion_payload(swagger_content)
    chunks = _build_swagger_chunks(parsed_text)

    assert len(chunks) == 3
    assert [chunk["subtype"] for chunk in chunks] == [
        "primary",
        "request_schema",
        "response_schema",
    ]
    assert "Request Schema Detail:" in str(chunks[1]["text"])
    assert "required fields: origin, name" in str(chunks[1]["text"])
    assert "top-level fields:" in str(chunks[1]["text"])
    assert "origin nested fields:" in str(chunks[1]["text"])
    assert "field path: origin.hostname" in str(chunks[1]["text"])
    assert "origin.hostname: string" in str(chunks[1]["text"])
    assert "cache nested fields:" in str(chunks[1]["text"])
    assert "field path: cache.disable" in str(chunks[1]["text"])
    assert "cache.disable: boolean" in str(chunks[1]["text"])
    assert "Response Schema Detail:" in str(chunks[2]["text"])
    assert "200 application/json top-level fields:" in str(chunks[2]["text"])


def test_openapi_render_round_trip_preserves_operation_metadata() -> None:
    swagger_content = b"""
openapi: 3.0.0
info:
  title: Round Trip API
  version: "1.0"
paths:
  /users:
    get:
      summary: List users
      operationId: listUsers
      tags: [users]
      responses:
        "200":
          description: OK
"""
    parsed_text, original_chunks, source_format, spec_version = build_openapi_ingestion_payload(swagger_content)
    reparsed_chunks, reparsed_source_format, reparsed_spec_version = extract_openapi_chunks_from_rendered_text(parsed_text)

    assert reparsed_source_format == source_format
    assert reparsed_spec_version == spec_version
    assert len(reparsed_chunks) == len(original_chunks) == 1
    assert reparsed_chunks[0].path == original_chunks[0].path
    assert reparsed_chunks[0].method == original_chunks[0].method
    assert reparsed_chunks[0].operation_id == original_chunks[0].operation_id
    assert tuple(reparsed_chunks[0].tags) == tuple(original_chunks[0].tags)


def test_openapi_structured_meta_empty_lists_override_text_fallback() -> None:
    swagger_content = b"""
openapi: 3.0.0
info:
  title: Empty Meta API
  version: "1.0"
paths:
  /users:
    get:
      summary: List users
      operationId: listUsers
      tags: [users]
      responses:
        "200":
          description: OK
    """
    parsed_text, _, _, _ = build_openapi_ingestion_payload(swagger_content)
    meta_line = next(
        line for line in parsed_text.splitlines() if line.startswith(OPENAPI_CHUNK_META_PREFIX)
    )
    meta = json.loads(meta_line.removeprefix(OPENAPI_CHUNK_META_PREFIX))
    meta["tags"] = []
    meta["response_codes"] = []
    meta["content_types"] = []
    meta["auth_schemes"] = []
    patched_text = parsed_text.replace(
        meta_line,
        f"{OPENAPI_CHUNK_META_PREFIX}{json.dumps(meta, ensure_ascii=False, sort_keys=True)}",
    )

    reparsed_chunks, _, _ = extract_openapi_chunks_from_rendered_text(patched_text)

    assert len(reparsed_chunks) == 1
    assert reparsed_chunks[0].tags == []
    assert reparsed_chunks[0].response_codes == []
    assert reparsed_chunks[0].content_types == []
    assert reparsed_chunks[0].auth_schemes == []


def test_openapi_examples_sanitize_internal_chunk_markers() -> None:
    swagger_content = f"""
openapi: 3.0.0
info:
  title: Marker API
  version: "1.0"
paths:
  /users:
    post:
      summary: Create user
      requestBody:
        required: true
        content:
          application/json:
            example:
              note: "before{chr(10)}{chr(10)}<<<OPENAPI_OPERATION>>>{chr(10)}{chr(10)}after"
      responses:
        "200":
          description: OK
""".encode()
    parsed_text, chunks, _, _ = build_openapi_ingestion_payload(swagger_content)
    reparsed_chunks, _, _ = extract_openapi_chunks_from_rendered_text(parsed_text)

    assert len(chunks) == 1
    assert len(reparsed_chunks) == 1
    assert parsed_text.count(OPENAPI_OPERATION_START_MARKER.strip()) == 1
    assert parsed_text.count(OPENAPI_CHUNK_META_PREFIX) == 1


def test_openapi_allof_ref_cycle_finishes_without_recursion_error() -> None:
    swagger_content = b"""
openapi: 3.0.0
info:
  title: Cycle API
  version: "1.0"
paths:
  /items:
    post:
      summary: Create item
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/A'
      responses:
        "200":
          description: OK
components:
  schemas:
    A:
      allOf:
        - $ref: '#/components/schemas/B'
        - type: object
          properties:
            id:
              type: string
    B:
      allOf:
        - $ref: '#/components/schemas/A'
        - type: object
          properties:
            name:
              type: string
"""
    parsed_text, chunks, _, _ = build_openapi_ingestion_payload(swagger_content)

    assert len(chunks) == 1
    assert "Endpoint: POST /items" in parsed_text


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_document_not_ready(
    mock_get_openai: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """400 if doc status=processing."""
    from backend.models import Document, DocumentStatus, DocumentType

    token = register_and_verify_user(tenant, db_session, email="proc@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Proc Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = cl_resp.json()["id"]

    doc = Document(
        tenant_id=uuid.UUID(tenant_id),
        filename="proc.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.processing,
        parsed_text="some text",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    doc_id = str(doc.id)

    response = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    mock_get_openai.assert_not_called()


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_openai_error(
    mock_get_openai: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """OpenAI raises exception → return 503."""
    token = register_and_verify_user(tenant, db_session, email="err@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Err Tenant"},
    )
    set_client_openai_key(tenant, token)
    md_content = b"# Test\n\nContent here."
    upload_resp = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("err.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    mock_client = Mock()
    mock_client.embeddings.create.side_effect = Exception("OpenAI API error")
    mock_get_openai.return_value = mock_client

    response = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    # Background task ran synchronously and set doc status to "error" after OpenAI failure
    doc_resp = tenant.get(
        f"/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert doc_resp.status_code == 200
    assert doc_resp.json()["status"] == "error"


@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_reruns_delete_old(
    mock_get_openai: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Call twice → old embeddings replaced."""
    token = register_and_verify_user(tenant, db_session, email="rerun@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Rerun Tenant"},
    )
    set_client_openai_key(tenant, token)
    md_content = b"# Doc\n\n" + b"x" * 600
    upload_resp = tenant.post(
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

    r1 = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 202

    r2 = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 202
    list_resp = tenant.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.json()["total_chunks"] == len(chunks)


@patch("backend.embeddings.service.get_openai_client")
def test_get_embeddings_success(
    mock_get_openai: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Get embeddings list for document."""
    token = register_and_verify_user(tenant, db_session, email="get@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Get Tenant"},
    )
    set_client_openai_key(tenant, token)
    md_content = b"# Get\n\nContent for embeddings."
    upload_resp = tenant.post(
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
    tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = tenant.get(
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
    tenant: TestClient,
    db_session: Session,
) -> None:
    """User B can't see user A's embeddings → 404."""
    token_a = register_and_verify_user(tenant, db_session, email="ga@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Tenant"},
    )
    set_client_openai_key(tenant, token_a)
    md_content = b"# Secret"
    upload_resp = tenant.post(
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
    tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    token_b = register_and_verify_user(tenant, db_session, email="gb@example.com")

    response = tenant.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


@patch("backend.embeddings.service.get_openai_client")
def test_delete_embeddings_success(
    mock_get_openai: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Delete all embeddings → returns count."""
    token = register_and_verify_user(tenant, db_session, email="del@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Del Emb Tenant"},
    )
    set_client_openai_key(tenant, token)
    md_content = b"# To delete\n\nContent."
    upload_resp = tenant.post(
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
    tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = tenant.delete(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == len(chunks)

    list_resp = tenant.get(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.json()["total_chunks"] == 0
