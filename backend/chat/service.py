"""Business logic for RAG chat pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from sqlalchemy.orm import Session

PREVIEW_MAX_LEN = 120

from backend.core.openai_client import get_openai_client
from backend.models import Chat, Document, Embedding, Message, MessageRole
from backend.search.service import (
    VECTOR_CONFIDENCE_THRESHOLD,
    cosine_similarity,
    embed_query,
    keyword_search_chunks,
)


def retrieve_context(
    client_id: uuid.UUID,
    question: str,
    db: Session,
    api_key: str,
    top_k: int = 5,
) -> tuple[list[str], list[uuid.UUID], list[float], Literal["vector", "keyword", "none"]]:
    """
    Retrieve context chunks for RAG (reuses search logic, returns mode for debug).

    Returns:
        chunk_texts: List of chunk text strings.
        document_ids: List of document UUIDs (order matches chunk_texts).
        scores: List of scores (similarity for vector, match count for keyword).
        mode: "vector" | "keyword" | "none".
    """
    query_vector = embed_query(question, api_key=api_key)

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        meta = emb.metadata_json or {}
        vector = meta.get("vector")
        if not vector or not isinstance(vector, list):
            continue
        sim = cosine_similarity(query_vector, vector)
        scored.append((emb, sim))

    scored.sort(key=lambda x: x[1], reverse=True)

    if scored and scored[0][1] >= VECTOR_CONFIDENCE_THRESHOLD:
        results = scored[:top_k]
        mode: Literal["vector", "keyword", "none"] = "vector"
    else:
        keyword_results = keyword_search_chunks(client_id, question, top_k, db)
        if keyword_results:
            results = keyword_results
            mode = "keyword"
        else:
            results = []
            mode = "none"

    chunk_texts = [r[0].chunk_text or "" for r in results]
    document_ids = [r[0].document_id for r in results]
    scores = [r[1] for r in results]

    return (chunk_texts, document_ids, scores, mode)


def build_rag_prompt(question: str, context_chunks: list[str]) -> str:
    """
    Build prompt from question + retrieved context chunks.

    Args:
        question: User question.
        context_chunks: List of text chunks from search.

    Returns:
        Formatted prompt string for GPT.
    """
    system_rules = (
        "You are a technical support agent for the client's product (SaaS, API, docs).\n"
        "Rules:\n"
        "- Answer based ONLY on the provided context. If context mentions the topic, you MUST answer from it.\n"
        "- Do NOT claim you don't know when the context contains relevant info.\n"
        "- If uncertain, say so but still answer from the context.\n"
        "- For \"which setting\" / \"какая настройка\" or similar: name the exact setting/field as in docs; cite where it is (section/page/menu) if the context contains it.\n"
        "- Answer in the SAME LANGUAGE as the question (e.g. Russian if asked in Russian).\n"
    )
    if not context_chunks:
        return (
            f"{system_rules}\n\n"
            "Context:\n(none)\n\n"
            f"Question: {question}\n\n"
            "Answer:"
        )
    context_block = "\n\n---\n\n".join(context_chunks)
    return (
        f"{system_rules}\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )


def generate_answer(
    question: str,
    context_chunks: list[str],
    *,
    api_key: str,
) -> tuple[str, int]:
    """
    Call OpenAI gpt-4o-mini with RAG prompt.

    Args:
        question: User question.
        context_chunks: Retrieved context chunks.

    Returns:
        Tuple of (answer_text, total_tokens).
        If context_chunks is empty, returns ("I don't have information about this.", 0).
    """
    if not context_chunks:
        return ("I don't have information about this.", 0)

    prompt = build_rag_prompt(question, context_chunks)
    openai_client = get_openai_client(api_key)
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=500,
    )
    answer_text = response.choices[0].message.content or ""
    total_tokens = response.usage.total_tokens if response.usage else 0
    return (answer_text.strip(), total_tokens)


def process_chat_message(
    client_id: uuid.UUID,
    question: str,
    session_id: uuid.UUID,
    db: Session,
    *,
    api_key: str,
) -> tuple[str, list[uuid.UUID], int]:
    """
    Full RAG pipeline: search → prompt → generate → save → return.

    Args:
        client_id: Client ID for tenant isolation.
        question: User question.
        session_id: Chat session ID.
        db: Database session.

    Returns:
        Tuple of (answer, document_ids, tokens_used).
    """
    # 1. Retrieve context (chunks + document_ids)
    chunk_texts, doc_ids, _scores, _mode = retrieve_context(
        client_id, question, db, api_key, top_k=5
    )
    document_ids = list(dict.fromkeys(doc_ids))

    # 2. Build RAG prompt
    prompt = build_rag_prompt(question, chunk_texts)

    # 3. Generate answer
    answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)

    # 4. Find or create Chat
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        chat = Chat(client_id=client_id, session_id=session_id)
        db.add(chat)
        db.commit()
        db.refresh(chat)

    # 5. Save user message
    user_msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content=question,
    )
    db.add(user_msg)

    # 6. Save assistant message
    # SQLite doesn't support ARRAY bind; use None for tests, document_ids for PostgreSQL
    source_docs = document_ids if "postgresql" in str(db.bind.url) else None
    assistant_msg = Message(
        chat_id=chat.id,
        role=MessageRole.assistant,
        content=answer,
        source_documents=source_docs,
    )
    db.add(assistant_msg)

    # 7. Save tokens_used on Chat (per message exchange)
    chat.tokens_used = tokens_used
    db.add(chat)
    db.commit()

    return (answer, document_ids, tokens_used)


def run_debug(
    client_id: uuid.UUID,
    question: str,
    db: Session,
    *,
    api_key: str,
) -> tuple[str, int, dict]:
    """
    Run RAG pipeline for debug: retrieval + answer, no DB persistence.

    Returns:
        Tuple of (answer, tokens_used, debug_dict).
        debug_dict: {"mode": str, "chunks": [{"document_id": str, "score": float, "preview": str}]}
    """
    chunk_texts, document_ids, scores, mode = retrieve_context(
        client_id, question, db, api_key, top_k=5
    )
    answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)

    chunks_debug = [
        {
            "document_id": str(doc_id),
            "score": score,
            "preview": (text[:200] + "..." if len(text) > 200 else text),
        }
        for doc_id, score, text in zip(document_ids, scores, chunk_texts)
    ]

    debug = {"mode": mode, "chunks": chunks_debug}
    return (answer, tokens_used, debug)


def get_chat_history(
    session_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> list[Message]:
    """
    Get all messages for a chat session (ownership enforced).

    Args:
        session_id: Chat session ID.
        client_id: Client ID for ownership check.
        db: Database session.

    Returns:
        List of Message objects, or empty list if not found/not owner.
    """
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        return []

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return list(messages)


@dataclass
class SessionSummary:
    """Summary of a chat session for inbox list."""

    session_id: uuid.UUID
    message_count: int
    last_question: Optional[str]
    last_answer_preview: Optional[str]
    last_activity: datetime


def list_chat_sessions(client_id: uuid.UUID, db: Session) -> list[SessionSummary]:
    """
    List all chat sessions for a client, sorted by last_activity DESC.

    Args:
        client_id: Client ID for tenant isolation.
        db: Database session.

    Returns:
        List of SessionSummary, sorted by last_activity descending.
    """
    chats = (
        db.query(Chat)
        .filter(Chat.client_id == client_id)
        .all()
    )
    result: list[SessionSummary] = []
    for chat in chats:
        messages = (
            db.query(Message)
            .filter(Message.chat_id == chat.id)
            .order_by(Message.created_at.asc())
            .all()
        )
        msg_count = len(messages)
        last_activity = datetime.min
        last_question: str | None = None
        last_answer_preview: str | None = None

        for m in messages:
            if m.created_at and m.created_at > last_activity:
                last_activity = m.created_at
            if m.role == MessageRole.user:
                last_question = m.content
            elif m.role == MessageRole.assistant:
                preview = m.content
                if len(preview) > PREVIEW_MAX_LEN:
                    preview = preview[:PREVIEW_MAX_LEN].rstrip() + "..."
                last_answer_preview = preview

        if msg_count > 0:
            result.append(
                SessionSummary(
                    session_id=chat.session_id,
                    message_count=msg_count,
                    last_question=last_question,
                    last_answer_preview=last_answer_preview,
                    last_activity=last_activity,
                )
            )
        else:
            result.append(
                SessionSummary(
                    session_id=chat.session_id,
                    message_count=0,
                    last_question=None,
                    last_answer_preview=None,
                    last_activity=chat.created_at or datetime.min,
                )
            )

    result.sort(key=lambda s: s.last_activity, reverse=True)
    return result


def get_session_logs(
    session_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> Optional[list[tuple[uuid.UUID, uuid.UUID, str, str, str, str | None, datetime]]]:
    """
    Get all messages for a session (ownership enforced).

    Args:
        session_id: Chat session ID.
        client_id: Client ID for ownership check.
        db: Database session.

    Returns:
        List of (message_id, session_id, role, content, feedback, ideal_answer, created_at)
        or None if not found.
    """
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        return None

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [
        (m.id, chat.session_id, m.role.value, m.content, m.feedback.value, m.ideal_answer, m.created_at)
        for m in messages
    ]
