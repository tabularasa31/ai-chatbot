"""FastAPI chat endpoints."""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from openai import APIError
from sqlalchemy.orm import Session

from backend.core.limiter import limiter
from backend.chat.schemas import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    MessageResponse,
)
from backend.chat.service import get_chat_history, process_chat_message
from backend.clients.service import get_client_by_api_key, get_client_by_user
from backend.core.db import get_db
from backend.auth.middleware import get_current_user
from backend.models import User

chat_router = APIRouter(tags=["chat"])


@chat_router.post("", response_model=ChatResponse)
@limiter.limit("30/minute")
def chat(
    request: Request,
    body: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> ChatResponse:
    """
    Chat endpoint (PUBLIC — no JWT, uses X-API-Key).

    Returns RAG-generated answer with source documents.
    Errors: 401 (invalid/missing API key), 422 (invalid question), 503 (OpenAI unavailable).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    client = get_client_by_api_key(x_api_key, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid API key")

    session_id = body.session_id or uuid.uuid4()

    try:
        answer, document_ids, tokens_used = process_chat_message(
            client_id=client.id,
            question=body.question,
            session_id=session_id,
            db=db,
        )
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        )

    return ChatResponse(
        answer=answer,
        session_id=session_id,
        source_documents=document_ids,
        tokens_used=tokens_used,
    )


@chat_router.get("/history/{session_id}", response_model=ChatHistoryResponse)
def get_history(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatHistoryResponse:
    """
    Get chat history for a session (protected JWT).

    Returns 404 if session not found or not owner.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    messages = get_chat_history(session_id, client.id, db)
    if not messages:
        raise HTTPException(status_code=404, detail="Session not found")

    return ChatHistoryResponse(
        session_id=session_id,
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role.value,
                content=m.content,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )
