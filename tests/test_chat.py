"""Tests for chat API (RAG pipeline)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

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


def test_generate_answer_no_context(mock_openai_client: Mock) -> None:
    """Empty chunks → fallback message, no OpenAI call."""
    answer, tokens = generate_answer("question", [], api_key="sk-test")
    assert answer == "I don't have information about this."
    assert tokens == 0
    mock_openai_client.chat.completions.create.assert_not_called()


def test_generate_answer_with_context(mock_openai_client: Mock) -> None:
    """With chunks, calls OpenAI and returns answer + tokens."""
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)

    answer, tokens = generate_answer("What?", ["chunk1"], api_key="sk-test")
    assert answer == "The answer is 42"
    assert tokens == 100
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-3.5-turbo"
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 500


# --- API tests ---


def test_chat_success(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Valid api_key + question → get answer back."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=50)

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


def test_chat_creates_messages_in_db(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """After chat, messages saved to DB."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Reply"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=10)

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


def test_chat_without_openai_key(client: TestClient) -> None:
    """400 if client has no OpenAI API key configured."""
    reg = client.post(
        "/auth/register",
        json={"email": "nokey@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Key Client"},
    )
    api_key = cl_resp.json()["api_key"]
    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 400
    assert "OpenAI API key" in response.json()["detail"]


def test_chat_empty_question(client: TestClient) -> None:
    """Empty string question → 422."""
    from tests.conftest import set_client_openai_key

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
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": ""},
    )
    assert response.status_code == 422


def test_chat_no_embeddings(mock_openai_client: Mock, client: TestClient) -> None:
    """No docs uploaded → answer is 'I don't have information'."""
    from tests.conftest import set_client_openai_key

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

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
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Anything"},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "I don't have information about this."
    assert response.json()["tokens_used"] == 0


def test_chat_uses_context(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Mock search returns chunk, verify it's in prompt."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.9] + [0.0] * 1535)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="99"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the secret?"},
    )
    assert response.status_code == 200
    assert "99" in response.json()["answer"]
    # Verify the chunk was passed to chat (via build_rag_prompt)
    call_args = mock_openai_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    assert len(messages) == 1
    assert "The secret number is 99" in messages[0]["content"]


def test_chat_session_continuity(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Two messages with same session_id → same chat in DB."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A1"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    r1 = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q1", "session_id": session_id},
    )
    assert r1.status_code == 200

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A2"))
    ]
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


def test_chat_new_session_auto_generated(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """No session_id → auto-generated UUID returned."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Hi"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=3)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hi"},
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    uuid.UUID(session_id)  # valid UUID


def test_get_history_success(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Get chat history after conversation."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Reply"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

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


def test_get_history_wrong_user(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """User B tries to get user A's session → 404."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token_a)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=1)

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


# --- Debug endpoint tests ---


def test_debug_with_embeddings_vector_mode(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Debug endpoint: embeddings with high similarity → mode vector, chunks returned."""
    from tests.conftest import set_client_openai_key
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "debugvec@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Vec Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=10)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "42"
    assert data["tokens_used"] == 10
    assert data["debug"]["mode"] == "vector"
    assert len(data["debug"]["chunks"]) >= 1
    chunk = data["debug"]["chunks"][0]
    assert chunk["document_id"] == str(doc.id)
    assert "score" in chunk
    assert chunk["score"] >= 0.3
    assert "preview" in chunk
    assert "42" in chunk["preview"] or "answer" in chunk["preview"].lower()


def test_debug_with_embeddings_keyword_mode(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Debug endpoint: low vector confidence → keyword fallback, mode keyword."""
    from tests.conftest import set_client_openai_key
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    reg = client.post(
        "/auth/register",
        json={"email": "debugkw@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Kw Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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
    db_session.add(emb)
    db_session.commit()

    # Query vector orthogonal to chunk → cosine = 0 → fallback to keyword
    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=query_vec)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="99"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "secret number"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["debug"]["mode"] == "keyword"
    assert len(data["debug"]["chunks"]) >= 1
    chunk = data["debug"]["chunks"][0]
    assert chunk["document_id"] == str(doc.id)
    assert chunk["score"] >= 1  # keyword returns match count
    assert "secret" in chunk["preview"].lower()


def test_debug_no_embeddings(
    mock_openai_client: Mock,
    client: TestClient,
) -> None:
    """Debug endpoint: no embeddings → mode none, chunks empty."""
    from tests.conftest import set_client_openai_key

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]

    reg = client.post(
        "/auth/register",
        json={"email": "debugnone@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug None Client"},
    )
    set_client_openai_key(client, token)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Anything"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "I don't have information about this."
    assert data["tokens_used"] == 0
    assert data["debug"]["mode"] == "none"
    assert data["debug"]["chunks"] == []


def test_debug_does_not_persist_chat(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """Debug runs do NOT create Chat/Message records."""
    from tests.conftest import set_client_openai_key
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    reg = client.post(
        "/auth/register",
        json={"email": "debugnopersist@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Persist Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Hello"},
    )
    assert response.status_code == 200

    # No Chat/Message should have been created
    chats = db_session.query(Chat).filter(Chat.client_id == client_id).all()
    assert len(chats) == 0
    messages = db_session.query(Message).all()
    assert len(messages) == 0


def test_debug_requires_auth(client: TestClient) -> None:
    """Debug endpoint requires JWT."""
    response = client.post(
        "/chat/debug",
        json={"question": "Hello"},
    )
    assert response.status_code == 401


def test_debug_empty_question(client: TestClient) -> None:
    """Debug with empty question → 422."""
    from tests.conftest import set_client_openai_key

    reg = client.post(
        "/auth/register",
        json={"email": "debugempty@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    set_client_openai_key(client, token)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": ""},
    )
    assert response.status_code == 422


def test_chat_openai_unavailable_503(
    mock_openai_client: Mock,
    client: TestClient,
    db_session,
) -> None:
    """OpenAI API error → 503."""
    from tests.conftest import set_client_openai_key
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
    set_client_openai_key(client, token)
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

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = APIError(
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
