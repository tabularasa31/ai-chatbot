"""Tests for chat API (RAG pipeline)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from backend.chat.service import build_rag_prompt, generate_answer


# --- Unit tests ---


def test_build_rag_prompt() -> None:
    """build_rag_prompt produces correct format with chunks."""
    chunks = ["chunk1", "chunk2", "chunk3"]
    result = build_rag_prompt("What is X?", chunks)
    assert "You are a helpful assistant" in result
    assert "Answer based ONLY on the provided context" in result
    assert "chunk1" in result
    assert "chunk2" in result
    assert "chunk3" in result
    assert "---" in result
    assert "Question: What is X?" in result
    assert "Answer:" in result


def test_build_rag_prompt_empty_chunks() -> None:
    """build_rag_prompt handles empty chunks."""
    result = build_rag_prompt("Q?", [])
    assert "Question: Q?" in result
    assert "(none)" in result


@patch("backend.chat.service.openai_client.chat.completions.create")
def test_generate_answer_no_context(mock_chat: Mock) -> None:
    """Empty chunks → fallback message, no OpenAI call."""
    answer, tokens = generate_answer("question", [])
    assert answer == "I don't have information about this."
    assert tokens == 0
    mock_chat.assert_not_called()


@patch("backend.chat.service.openai_client.chat.completions.create")
def test_generate_answer_with_context(mock_chat: Mock) -> None:
    """With chunks, calls OpenAI and returns answer + tokens."""
    mock_chat.return_value.choices = [Mock(message=Mock(content="The answer is 42"))]
    mock_chat.return_value.usage = Mock(total_tokens=100)

    answer, tokens = generate_answer("What?", ["chunk1"])
    assert answer == "The answer is 42"
    assert tokens == 100
    mock_chat.assert_called_once()
    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["model"] == "gpt-3.5-turbo"
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 500


# --- API tests ---


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_success(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Valid api_key + question → get answer back."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "chat@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Chat Client"},
    )
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="chat.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="The answer is 42",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="The answer is 42"))]
    mock_chat.return_value.usage = Mock(total_tokens=50)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "The answer is 42"
    assert "session_id" in data
    assert data["source_documents"] == [str(doc.id)]
    assert data["tokens_used"] == 50


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_creates_messages_in_db(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """After chat, messages saved to DB."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    reg = client.post(
        "/auth/register",
        json={"email": "msg@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Msg Client"},
    )
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="msg.md",
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

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="Reply"))]
    mock_chat.return_value.usage = Mock(total_tokens=10)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 200
    session_id = uuid.UUID(response.json()["session_id"])

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).first()
    assert chat is not None
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 2
    roles = [m.role.value for m in messages]
    assert "user" in roles
    assert "assistant" in roles


def test_chat_invalid_api_key(client: TestClient) -> None:
    """Wrong api_key → 401."""
    response = client.post(
        "/chat",
        headers={"X-API-Key": "invalid-key-12345"},
        json={"question": "Hello"},
    )
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


def test_chat_missing_api_key(client: TestClient) -> None:
    """No X-API-Key header → 401."""
    response = client.post(
        "/chat",
        json={"question": "Hello"},
    )
    assert response.status_code == 401


def test_chat_empty_question(client: TestClient) -> None:
    """Empty string question → 422."""
    reg = client.post(
        "/auth/register",
        json={"email": "empty@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": ""},
    )
    assert response.status_code == 422


@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_no_embeddings(mock_embed: Mock, client: TestClient) -> None:
    """No docs uploaded → answer is 'I don't have information'."""
    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]

    reg = client.post(
        "/auth/register",
        json={"email": "noemb@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Emb Client"},
    )
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Anything"},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "I don't have information about this."
    assert response.json()["tokens_used"] == 0


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_uses_context(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Mock search returns chunk, verify it's in prompt."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "ctx@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Ctx Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    doc = Document(
        client_id=client_id,
        filename="ctx.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Secret answer: 99",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="The secret number is 99.",
        vector=None,
        metadata_json={"vector": [0.9] + [0.0] * 1535, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_embed.return_value.data = [Mock(embedding=[0.9] + [0.0] * 1535)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="99"))]
    mock_chat.return_value.usage = Mock(total_tokens=5)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the secret?"},
    )
    assert response.status_code == 200
    assert "99" in response.json()["answer"]
    # Verify the chunk was passed to chat (via build_rag_prompt)
    call_args = mock_chat.call_args
    messages = call_args.kwargs["messages"]
    assert len(messages) == 1
    assert "The secret number is 99" in messages[0]["content"]


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_session_continuity(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Two messages with same session_id → same chat in DB."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    reg = client.post(
        "/auth/register",
        json={"email": "cont@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Cont Client"},
    )
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])
    session_id = str(uuid.uuid4())

    doc = Document(
        client_id=client_id,
        filename="cont.md",
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

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="A1"))]
    mock_chat.return_value.usage = Mock(total_tokens=5)

    r1 = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q1", "session_id": session_id},
    )
    assert r1.status_code == 200

    mock_chat.return_value.choices = [Mock(message=Mock(content="A2"))]
    r2 = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q2", "session_id": session_id},
    )
    assert r2.status_code == 200

    chat = db_session.query(Chat).filter(
        Chat.session_id == uuid.UUID(session_id),
    ).first()
    assert chat is not None
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 4  # Q1, A1, Q2, A2


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_new_session_auto_generated(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """No session_id → auto-generated UUID returned."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "auto@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Auto Client"},
    )
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="auto.md",
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

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="Hi"))]
    mock_chat.return_value.usage = Mock(total_tokens=3)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hi"},
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    uuid.UUID(session_id)  # valid UUID


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_get_history_success(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Get chat history after conversation."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "hist@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Hist Client"},
    )
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="hist.md",
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

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="Reply"))]
    mock_chat.return_value.usage = Mock(total_tokens=5)

    chat_resp = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    session_id = chat_resp.json()["session_id"]

    hist_resp = client.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hist_resp.status_code == 200
    data = hist_resp.json()
    assert data["session_id"] == session_id
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "Hello"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Reply"


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_get_history_wrong_user(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """User B tries to get user A's session → 404."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg_a = client.post(
        "/auth/register",
        json={"email": "userA@example.com", "password": "SecurePass1!"},
    )
    token_a = reg_a.json()["token"]
    cl_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    api_key_a = cl_a.json()["api_key"]
    client_id_a = uuid.UUID(cl_a.json()["id"])
    session_id = str(uuid.uuid4())

    doc = Document(
        client_id=client_id_a,
        filename="a.md",
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

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.return_value.choices = [Mock(message=Mock(content="A"))]
    mock_chat.return_value.usage = Mock(total_tokens=1)

    client.post(
        "/chat",
        headers={"X-API-Key": api_key_a},
        json={"question": "Hi", "session_id": session_id},
    )

    reg_b = client.post(
        "/auth/register",
        json={"email": "userB@example.com", "password": "SecurePass1!"},
    )
    token_b = reg_b.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )

    hist_resp = client.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert hist_resp.status_code == 404


def test_get_history_unauthenticated(client: TestClient) -> None:
    """No JWT → 401."""
    session_id = str(uuid.uuid4())
    response = client.get(f"/chat/history/{session_id}")
    assert response.status_code == 401


@patch("backend.chat.service.openai_client.chat.completions.create")
@patch("backend.search.service.openai_client.embeddings.create")
def test_chat_openai_unavailable_503(
    mock_embed: Mock,
    mock_chat: Mock,
    client: TestClient,
    db_session,
) -> None:
    """OpenAI API error → 503."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    from openai import APIError

    reg = client.post(
        "/auth/register",
        json={"email": "err@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Err Client"},
    )
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="err.md",
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

    mock_embed.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_chat.side_effect = APIError(
        "Service unavailable",
        request=Mock(),
        body=None,
    )

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 503
    assert "OpenAI" in response.json()["detail"]
