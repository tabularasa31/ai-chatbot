"""Tests for the entity-index wiring on the embeddings ingest path.

Step 4 of the entity-aware retrieval epic (ClickUp 86exe5pjx). Covers:

- ``Embedding.entities`` defaults to ``[]`` and is NOT NULL.
- ``create_embeddings_for_document`` populates the column from
  ``extract_entities_from_passage`` output.
- A NER outage (raised exception) does not abort embedding ingest —
  embeddings are committed first, the entity-fill loop degrades to ``[]``
  per chunk, and the document still ends up with retrievable rows.
- An NER call returning the documented sentinel (``[]``) writes an empty
  list, never NULL — so the Step 5 retriever's ``entities ?| array[...]``
  predicate stays trivially safe.

OpenAI is fully mocked. SQLite-only fast lane.
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Embedding
from tests.conftest import register_and_verify_user, set_client_openai_key


def _setup_tenant_with_doc(
    tenant: TestClient,
    db_session: Session,
    *,
    email: str,
    body: bytes = b"# Title\n\nLorem ipsum dolor sit amet.",
) -> tuple[str, str]:
    """Register user, create tenant + key, upload one markdown doc.

    Returns (token, document_id).
    """
    token = register_and_verify_user(tenant, db_session, email=email)
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Entities Tenant"},
    )
    set_client_openai_key(tenant, token)
    upload = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("ent.md", body, "text/markdown")},
    )
    return token, upload.json()["id"]


def _read_embeddings(document_id: str) -> list[Embedding]:
    from backend.core.db import SessionLocal

    with SessionLocal() as fresh:
        return (
            fresh.query(Embedding)
            .filter(Embedding.document_id == uuid.UUID(document_id))
            .order_by(Embedding.created_at.asc())
            .all()
        )


# ── happy path ───────────────────────────────────────────────────────────────


@patch("backend.embeddings.service.extract_entities_from_passage")
@patch("backend.embeddings.service.get_openai_client")
def test_entities_populated_from_ner(
    mock_get_openai: Mock,
    mock_extract: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """NER output flows into Embedding.entities as a list."""
    body = b"# Title\n\n" + b"Pro plan in Acme CRM costs 59 USD. " * 30
    token, doc_id = _setup_tenant_with_doc(tenant, db_session, email="ent1@example.com", body=body)

    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(50)]
    )
    mock_get_openai.return_value = mock_client

    # Each chunk gets a distinct entity list — the loop iterates per chunk,
    # so we just return a constant set per call. Cycling per call would also
    # work but a constant return makes the assertion crisp.
    mock_extract.return_value = ["Pro plan", "Acme CRM"]

    resp = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202

    rows = _read_embeddings(doc_id)
    assert len(rows) >= 1
    for row in rows:
        assert row.entities == ["Pro plan", "Acme CRM"]
    # NER was called once per chunk.
    assert mock_extract.call_count == len(rows)


# ── NER outage / failure ─────────────────────────────────────────────────────


@patch("backend.embeddings.service.extract_entities_from_passage")
@patch("backend.embeddings.service.get_openai_client")
def test_ner_raise_does_not_abort_ingest(
    mock_get_openai: Mock,
    mock_extract: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """If extract_entities_from_passage raises, ingest still completes.

    The function is documented to swallow its own errors and return [],
    but defense in depth in _populate_entities_for_embeddings catches
    anything that leaks. Either way, the document must end up with
    retrievable embeddings (entities=[] is acceptable).
    """
    body = b"# Title\n\n" + b"some text. " * 50
    token, doc_id = _setup_tenant_with_doc(tenant, db_session, email="ent2@example.com", body=body)

    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(50)]
    )
    mock_get_openai.return_value = mock_client
    mock_extract.side_effect = RuntimeError("ner outage simulation")

    resp = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202

    rows = _read_embeddings(doc_id)
    assert len(rows) >= 1
    for row in rows:
        # Empty list — never NULL.
        assert row.entities == []


# ── NER returns empty list (control / no-entities query) ─────────────────────


@patch("backend.embeddings.service.extract_entities_from_passage")
@patch("backend.embeddings.service.get_openai_client")
def test_ner_empty_writes_empty_list(
    mock_get_openai: Mock,
    mock_extract: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Documented sentinel ([]) is persisted as an empty list, never NULL."""
    body = b"# Title\n\nGeneric onboarding text without specific names. " * 30
    token, doc_id = _setup_tenant_with_doc(tenant, db_session, email="ent3@example.com", body=body)

    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(50)]
    )
    mock_get_openai.return_value = mock_client
    mock_extract.return_value = []

    resp = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202

    rows = _read_embeddings(doc_id)
    assert len(rows) >= 1
    for row in rows:
        assert row.entities == []
        assert row.entities is not None


# ── ORM default for direct row insert ────────────────────────────────────────


def test_entities_default_empty_list_on_direct_insert(db_session: Session) -> None:
    """Inserting an Embedding without explicit entities yields []."""
    from backend.models import Document, DocumentStatus, DocumentType, Tenant

    tenant = Tenant(name="Direct", public_id="direct-pub")
    db_session.add(tenant)
    db_session.flush()
    doc = Document(
        tenant_id=tenant.id,
        filename="d.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="x",
    )
    db_session.add(doc)
    db_session.flush()
    emb = Embedding(document_id=doc.id, chunk_text="hello", vector=None, metadata_json={})
    db_session.add(emb)
    db_session.commit()
    db_session.refresh(emb)
    assert emb.entities == []


# ── Per-chunk attribution telemetry passthrough ──────────────────────────────


@patch("backend.embeddings.service.extract_entities_from_passage")
@patch("backend.embeddings.service.get_openai_client")
def test_ner_receives_tenant_id_for_telemetry(
    mock_get_openai: Mock,
    mock_extract: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """tenant_id is threaded through so retry telemetry attributes correctly."""
    body = b"# Title\n\n" + b"some text. " * 30
    token, doc_id = _setup_tenant_with_doc(tenant, db_session, email="ent4@example.com", body=body)

    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(50)]
    )
    mock_get_openai.return_value = mock_client
    mock_extract.return_value = ["x"]

    tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Every call to extract_entities_from_passage carries a non-None tenant_id.
    assert mock_extract.call_count >= 1
    for call in mock_extract.call_args_list:
        kwargs = call.kwargs
        assert kwargs.get("tenant_id") is not None
        assert isinstance(kwargs["tenant_id"], str)
