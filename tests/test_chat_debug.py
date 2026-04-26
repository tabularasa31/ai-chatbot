"""Tests for the /chat/debug endpoint."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.guards.reject_response import RejectReason, build_reject_response
from tests.chat_utils import _bot_public_id, _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_debug_with_embeddings_vector_mode(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Debug endpoint keeps vector confidence separate from final retrieval mode."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="debugvec@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Vec Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="debug.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="The answer is 42 from vector search",
        vector=None,
        metadata_json={"vector": [0.9] + [0.0] * 1535, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.9] + [0.0] * 1535)
    ]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "42",
        total_tokens=10,
    )

    response = tenant.post(
        f"/chat/debug?bot_id={_bot_public_id(tenant, token)}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "42"
    assert data["tokens_used"] == 10
    assert data["debug"]["mode"] == "hybrid"
    assert data["debug"]["confidence_source"] == "vector_similarity"
    assert data["debug"]["best_confidence_score"] > 0.0
    assert data["debug"]["contradiction_detected"] is False
    assert data["debug"]["contradiction_count"] == 0
    assert data["debug"]["contradiction_pair_count"] == 0
    assert data["debug"]["contradiction_basis_types"] == []
    assert data["debug"]["contradiction_adjudication_enabled"] is False
    assert data["debug"]["contradiction_adjudication_status"] == "skipped_no_candidates"
    assert data["debug"]["contradiction_adjudication_candidate_count"] == 0
    assert data["debug"]["contradiction_adjudication_sent_count"] == 0
    assert len(data["debug"]["chunks"]) >= 1
    chunk = data["debug"]["chunks"][0]
    assert chunk["document_id"] == str(doc.id)
    assert "score" in chunk
    assert chunk["score"] >= 0.3
    assert "preview" in chunk
    assert "42" in chunk["preview"] or "answer" in chunk["preview"].lower()


def test_debug_response_includes_adjudication_fields_and_reliability_payload(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="debugadj@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Adjudication Tenant"},
    )
    set_client_openai_key(tenant, token)

    monkeypatch.setattr(
        "backend.chat.routes.run_debug",
        lambda **kwargs: (
            "42",
            10,
            {
                "mode": "hybrid",
                "best_rank_score": 0.9,
                "best_confidence_score": 0.9,
                "confidence_source": "vector_similarity",
                "contradiction_detected": True,
                "contradiction_count": 1,
                "contradiction_pair_count": 1,
                "contradiction_basis_types": ["effective_date"],
                "contradiction_adjudication_enabled": True,
                "contradiction_adjudication_applied_to_any_fact": True,
                "contradiction_adjudication_status": "completed",
                "contradiction_adjudication_candidate_count": 1,
                "contradiction_adjudication_sent_count": 1,
                "contradiction_adjudication_completed_count": 1,
                "contradiction_adjudication_confirmed_count": 1,
                "contradiction_adjudication_rejected_count": 0,
                "contradiction_adjudication_inconclusive_count": 0,
                "contradiction_adjudication_error_count": 0,
                "reliability": {
                    "score": "medium",
                    "evidence": {
                        "contradiction": {"pairs": [{"basis": "effective_date"}]},
                        "contradiction_adjudication": {
                            "status": "completed",
                            "items": [{"fact_id": "fact_001"}],
                        },
                    },
                },
                "chunks": [
                    {
                        "document_id": str(uuid.uuid4()),
                        "score": 0.9,
                        "preview": "preview",
                    }
                ],
                "validation": {"is_valid": True},
            },
        ),
    )

    response = tenant.post(
        f"/chat/debug?bot_id={_bot_public_id(tenant, token)}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What changed?"},
    )
    assert response.status_code == 200
    debug = response.json()["debug"]
    assert debug["contradiction_adjudication_enabled"] is True
    assert debug["contradiction_adjudication_applied_to_any_fact"] is True
    assert debug["contradiction_adjudication_status"] == "completed"
    assert debug["contradiction_adjudication_candidate_count"] == 1
    assert debug["contradiction_adjudication_sent_count"] == 1
    assert debug["contradiction_adjudication_completed_count"] == 1
    assert debug["contradiction_adjudication_confirmed_count"] == 1
    assert debug["reliability"]["evidence"]["contradiction_adjudication"]["status"] == "completed"


def test_debug_with_embeddings_keyword_mode(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Debug endpoint: low vector confidence → keyword fallback, mode keyword."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="debugkw@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Kw Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="debugkw.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    # Chunk with keyword "secret" - use orthogonal vectors so cosine < 0.3
    chunk_vec = [0.0, 1.0] + [0.0] * 1534
    emb = Embedding(
        document_id=doc.id,
        chunk_text="The secret number is 99. Very secret.",
        vector=None,
        metadata_json={"vector": chunk_vec, "chunk_index": 0},
    )
    decoy = Embedding(
        document_id=doc.id,
        chunk_text="Billing overview and invoice export steps.",
        vector=None,
        metadata_json={"vector": chunk_vec, "chunk_index": 1},
    )
    second_decoy = Embedding(
        document_id=doc.id,
        chunk_text="Password rotation policy for administrators only.",
        vector=None,
        metadata_json={"vector": chunk_vec, "chunk_index": 2},
    )
    db_session.add_all([emb, decoy, second_decoy])
    db_session.commit()

    # Query vector orthogonal to chunk → vector confidence 0, lexical signal drives ranking.
    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=query_vec)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="99"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = tenant.post(
        f"/chat/debug?bot_id={_bot_public_id(tenant, token)}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "secret number"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["debug"]["mode"] == "hybrid"
    assert data["debug"]["confidence_source"] == "vector_similarity"
    assert data["debug"]["best_confidence_score"] == 0.0
    assert len(data["debug"]["chunks"]) >= 1
    chunk = data["debug"]["chunks"][0]
    assert chunk["document_id"] == str(doc.id)
    assert chunk["score"] > 0.0
    assert "secret" in chunk["preview"].lower()


def test_debug_no_embeddings(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Debug endpoint: no embeddings → mode none, chunks empty."""
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]

    token = register_and_verify_user(tenant, db_session, email="debugnone@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug None Tenant"},
    )
    set_client_openai_key(tenant, token)

    response = tenant.post(
        f"/chat/debug?bot_id={_bot_public_id(tenant, token)}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Anything"},
    )
    assert response.status_code == 200
    data = response.json()
    # No embeddings → validation fallback → INSUFFICIENT_CONFIDENCE text
    expected = build_reject_response(reason=RejectReason.INSUFFICIENT_CONFIDENCE, profile=None)
    assert data["answer"] == expected
    # English response_language keeps the canonical fallback text, so no extra
    # localization tokens are counted.
    assert data["tokens_used"] == 0
    assert data["debug"]["mode"] == "none"
    assert data["debug"]["chunks"] == []
    assert data["debug"]["validation_outcome"] == "fallback"


def test_debug_does_not_persist_chat(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Debug runs do NOT create Chat/Message records."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(tenant, db_session, email="debugnopersist@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Persist Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="debugnp.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="chunk",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Reply"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = tenant.post(
        f"/chat/debug?bot_id={_bot_public_id(tenant, token)}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Hello"},
    )
    assert response.status_code == 200

    # No Chat/Message should have been created
    chats = db_session.query(Chat).filter(Chat.tenant_id == tenant_id).all()
    assert len(chats) == 0
    messages = db_session.query(Message).all()
    assert len(messages) == 0


def test_debug_requires_auth(tenant: TestClient) -> None:
    """Debug endpoint requires JWT."""
    response = tenant.post(
        "/chat/debug?bot_id=ch_testbot",
        json={"question": "Hello"},
    )
    assert response.status_code == 401


def test_debug_empty_question(tenant: TestClient, db_session: Session) -> None:
    """Debug with empty question → 422."""
    token = register_and_verify_user(tenant, db_session, email="debugempty@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Tenant"},
    )
    set_client_openai_key(tenant, token)

    response = tenant.post(
        f"/chat/debug?bot_id={_bot_public_id(tenant, token)}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": ""},
    )
    assert response.status_code == 422
