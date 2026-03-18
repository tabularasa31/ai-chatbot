"""Business logic for RAG chat pipeline."""

from __future__ import annotations

import uuid

from openai import OpenAI
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models import Chat, Message, MessageRole
from backend.search.service import search_similar_chunks

openai_client = OpenAI(api_key=settings.openai_api_key)


def build_rag_prompt(question: str, context_chunks: list[str]) -> str:
    """
    Build prompt from question + retrieved context chunks.

    Args:
        question: User question.
        context_chunks: List of text chunks from search.

    Returns:
        Formatted prompt string for GPT.
    """
    if not context_chunks:
        return (
            "You are a helpful assistant. Answer based ONLY on the provided context.\n"
            "If the answer is not in the context, say 'I don't have information about this.'\n\n"
            "Context:\n(none)\n\n"
            f"Question: {question}\n\n"
            "Answer:"
        )
    context_block = "\n\n---\n\n".join(context_chunks)
    return (
        "You are a helpful assistant. Answer based ONLY on the provided context.\n"
        "If the answer is not in the context, say 'I don't have information about this.'\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )


def generate_answer(question: str, context_chunks: list[str]) -> tuple[str, int]:
    """
    Call OpenAI GPT-3.5-turbo with RAG prompt.

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
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
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
    # 1. Search similar chunks
    results = search_similar_chunks(client_id, question, top_k=3, db=db)

    # 2. Extract chunk_text and document_ids
    chunk_texts = [r[0].chunk_text for r in results]
    document_ids = list(dict.fromkeys(r[0].document_id for r in results))

    # 3. Build RAG prompt
    prompt = build_rag_prompt(question, chunk_texts)

    # 4. Generate answer
    answer, tokens_used = generate_answer(question, chunk_texts)

    # 5. Find or create Chat
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        chat = Chat(client_id=client_id, session_id=session_id)
        db.add(chat)
        db.commit()
        db.refresh(chat)

    # 6. Save user message
    user_msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content=question,
    )
    db.add(user_msg)

    # 7. Save assistant message
    # SQLite doesn't support ARRAY bind; use None for tests, document_ids for PostgreSQL
    source_docs = document_ids if "postgresql" in str(db.bind.url) else None
    assistant_msg = Message(
        chat_id=chat.id,
        role=MessageRole.assistant,
        content=answer,
        source_documents=source_docs,
    )
    db.add(assistant_msg)
    db.commit()

    return (answer, document_ids, tokens_used)


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
