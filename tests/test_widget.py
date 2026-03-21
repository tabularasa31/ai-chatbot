"""Tests for public widget routes (/widget/*)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Document, DocumentStatus, DocumentType, Embedding
from tests.conftest import register_and_verify_user, set_client_openai_key


def _widget_url(public_id: str, message: str = "hello") -> str:
    from urllib.parse import quote

    return f"/widget/chat?message={quote(message)}&client_id={public_id}"


def _seed_rag_chunk(db_session: Session, client_uuid: uuid.UUID) -> None:
    """One ready document + embedding so RAG returns context (SQLite test path)."""
    doc = Document(
        client_id=client_uuid,
        filename="widget.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="widget support content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="widget support content",
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db_session.commit()


def test_widget_chat_success(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Happy path: public widget chat returns answer and session_id."""
    token = register_and_verify_user(client, db_session, email="widget-ok@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Ok Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    body = cl_resp.json()
    public_id = body["public_id"]
    client_uuid = uuid.UUID(body["id"])
    _seed_rag_chunk(db_session, client_uuid)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Widget says hi"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    r = client.post(_widget_url(public_id, message="widget support"))
    assert r.status_code == 200
    data = r.json()
    assert data["response"] == "Widget says hi"
    assert "session_id" in data


def test_widget_chat_rate_limit_429_after_20_requests_same_ip(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """
    With a fixed rate-limit key, request 21 in the same window returns 429.

    Default test `Limiter` key_func uses a fresh UUID per call, so limits never
    accumulate; widget uses `widget_public_rate_limit_key` with an override hook.
    """
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    token = register_and_verify_user(client, db_session, email="widget-rl@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget RL Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    body = cl_resp.json()
    public_id = body["public_id"]
    client_uuid = uuid.UUID(body["id"])
    _seed_rag_chunk(db_session, client_uuid)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=2)

    set_widget_public_rate_limit_key_override(lambda _r: "test-widget-rate-limit-ip")
    try:
        for i in range(20):
            r = client.post(_widget_url(public_id, message=f"widget support {i}"))
            assert r.status_code == 200, f"request {i + 1}: {r.status_code} {r.text}"

        r21 = client.post(_widget_url(public_id, message="widget support over-limit"))
        assert r21.status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_widget_chat_unknown_public_id_404(client: TestClient) -> None:
    r = client.post("/widget/chat?message=hi&client_id=ch_doesnotexist000")
    assert r.status_code == 404
