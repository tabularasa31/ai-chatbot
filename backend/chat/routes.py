"""FastAPI chat endpoints."""

import uuid
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from openai import APIError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.limiter import limiter
from backend.chat.schemas import (
    ChatHistoryResponse,
    ChatMessageLogItem,
    ChatMessageLogResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionListResponse,
    ChatSessionSummaryResponse,
    MessageResponse,
)
from backend.chat.service import (
    get_chat_history,
    get_session_logs,
    list_chat_sessions,
    process_chat_message,
    run_debug,
)
from backend.clients.service import get_client_by_api_key, get_client_by_user
from backend.core.db import get_db
from backend.auth.middleware import get_current_user
from backend.models import User


class DebugRequest(BaseModel):
    """Request body for debug endpoint."""

    question: str = Field(..., min_length=1, max_length=1000)


class DebugChunkResponse(BaseModel):
    """Single chunk in debug response."""

    document_id: str
    score: float
    preview: str


class DebugInfoResponse(BaseModel):
    """Debug info for RAG retrieval."""

    mode: Literal["vector", "keyword", "none"]
    chunks: list[DebugChunkResponse]


class ChatDebugResponse(BaseModel):
    """Response from chat debug endpoint."""

    answer: str
    tokens_used: int
    debug: DebugInfoResponse

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
    if not client.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    session_id = body.session_id or uuid.uuid4()

    try:
        answer, document_ids, tokens_used = process_chat_message(
            client_id=client.id,
            question=body.question,
            session_id=session_id,
            db=db,
            api_key=client.openai_api_key,
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


@chat_router.post("/debug", response_model=ChatDebugResponse)
@limiter.limit("30/minute")
def chat_debug(
    request: Request,
    body: DebugRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatDebugResponse:
    """
    Debug endpoint: run RAG pipeline without persisting to DB.
    JWT auth required. Returns answer + retrieval debug info.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if not client.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    try:
        answer, tokens_used, debug_dict = run_debug(
            client_id=client.id,
            question=body.question,
            db=db,
            api_key=client.openai_api_key,
        )
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        )

    debug_resp = DebugInfoResponse(
        mode=debug_dict["mode"],
        chunks=[
            DebugChunkResponse(
                document_id=c["document_id"],
                score=c["score"],
                preview=c["preview"],
            )
            for c in debug_dict["chunks"]
        ],
    )
    return ChatDebugResponse(
        answer=answer,
        tokens_used=tokens_used,
        debug=debug_resp,
    )


@chat_router.get("/sessions", response_model=ChatSessionListResponse)
def get_sessions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatSessionListResponse:
    """
    List all chat sessions for the authenticated client (inbox-style).
    JWT auth required. Returns sessions sorted by last_activity DESC.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    summaries = list_chat_sessions(client.id, db)
    return ChatSessionListResponse(
        sessions=[
            ChatSessionSummaryResponse(
                session_id=s.session_id,
                message_count=s.message_count,
                last_question=s.last_question,
                last_answer_preview=s.last_answer_preview,
                last_activity=s.last_activity,
            )
            for s in summaries
        ],
    )


@chat_router.get("/logs/session/{session_id}", response_model=ChatMessageLogResponse)
def get_session_logs_route(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessageLogResponse:
    """
    Get full message log for a session (read-only).
    JWT auth required. Returns 404 if session not found or not owner.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    logs = get_session_logs(session_id, client.id, db)
    if logs is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return ChatMessageLogResponse(
        messages=[
            ChatMessageLogItem(
                session_id=sid,
                role=role,
                content=content,
                created_at=created_at,
            )
            for sid, role, content, created_at in logs
        ],
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
