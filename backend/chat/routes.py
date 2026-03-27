"""FastAPI chat endpoints."""

import uuid
from collections import defaultdict
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from openai import APIError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.limiter import limiter
from backend.chat.schemas import (
    BadAnswerItem,
    BadAnswerListResponse,
    ChatHistoryResponse,
    ChatMessageLogItem,
    ChatMessageLogResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionListResponse,
    ChatSessionSummaryResponse,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    MessageResponse,
)
from backend.escalation.schemas import ManualEscalateRequest, ManualEscalateResponse
from backend.escalation.service import perform_manual_escalation
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
from backend.models import Chat, EscalationTrigger, Message, MessageFeedback, MessageRole, User
from backend.models import PiiEvent, PiiEventDirection


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

    mode: Literal["vector", "keyword", "hybrid", "none"]
    best_rank_score: Optional[float] = None
    best_confidence_score: Optional[float] = None
    confidence_source: Optional[Literal["vector_similarity", "rank_score", "none"]] = None
    chunks: list[DebugChunkResponse]
    validation: Optional[dict] = None


class ChatDebugResponse(BaseModel):
    """Response from chat debug endpoint."""

    answer: str
    tokens_used: int
    debug: DebugInfoResponse

chat_router = APIRouter(tags=["chat"])


def _require_original_access(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Original content access requires admin privileges")


@chat_router.post("", response_model=ChatResponse)
@limiter.limit("30/minute")
def chat(
    request: Request,
    body: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
    x_browser_locale: Annotated[Optional[str], Header(alias="X-Browser-Locale")] = None,
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
        answer, document_ids, tokens_used, chat_ended = process_chat_message(
            client_id=client.id,
            question=body.question,
            session_id=session_id,
            db=db,
            api_key=client.openai_api_key,
            browser_locale=x_browser_locale,
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
        chat_ended=chat_ended,
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
        validation=debug_dict.get("validation"),
    )
    return ChatDebugResponse(
        answer=answer,
        tokens_used=tokens_used,
        debug=debug_resp,
    )


@chat_router.post("/{session_id}/escalate", response_model=ManualEscalateResponse)
@limiter.limit("30/minute")
def chat_escalate(
    request: Request,
    session_id: uuid.UUID,
    body: ManualEscalateRequest,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> ManualEscalateResponse:
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
    trig = (
        EscalationTrigger.user_request
        if body.trigger == "user_request"
        else EscalationTrigger.answer_rejected
    )
    try:
        msg, tnum = perform_manual_escalation(
            db,
            client,
            session_id,
            api_key=client.openai_api_key,
            user_note=body.user_note,
            trigger=trig,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    except APIError:
        raise HTTPException(status_code=503, detail="OpenAI service unavailable")
    return ManualEscalateResponse(message=msg, ticket_number=tnum)


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
    include_original: bool = Query(False),
) -> ChatMessageLogResponse:
    """
    Get full message log for a session (read-only).
    JWT auth required. Returns 404 if session not found or not owner.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if include_original:
        _require_original_access(current_user)

    logs = get_session_logs(session_id, client.id, db, include_original=include_original)
    if logs is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if include_original:
        for msg_id, _sid, _role, _content, content_original, content_original_available, _feedback, _ideal_answer, _created_at in logs:
            if not content_original_available or content_original is None:
                continue
            db.add(
                PiiEvent(
                    client_id=client.id,
                    chat_id=None,
                    message_id=msg_id,
                    direction=PiiEventDirection.original_view,
                    entity_type="ORIGINAL_VIEW",
                    count=1,
                )
            )
        db.commit()

    return ChatMessageLogResponse(
        messages=[
            ChatMessageLogItem(
                id=msg_id,
                session_id=sid,
                role=role,
                content=content,
                content_original=content_original,
                content_original_available=content_original_available,
                feedback=feedback,
                ideal_answer=ideal_answer,
                created_at=created_at,
            )
            for msg_id, sid, role, content, content_original, content_original_available, feedback, ideal_answer, created_at in logs
        ],
    )


@chat_router.post("/messages/{message_id}/feedback", response_model=MessageFeedbackResponse)
def set_message_feedback(
    message_id: uuid.UUID,
    body: MessageFeedbackRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MessageFeedbackResponse:
    """
    Set feedback (up/down) and optional ideal_answer on an assistant message.
    JWT auth required. 400 if message is not assistant, 404 if not found or not owner.
    """
    from sqlalchemy.orm import joinedload

    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    message = (
        db.query(Message)
        .options(joinedload(Message.chat))
        .filter(Message.id == message_id)
        .first()
    )
    if not message or message.chat.client_id != client.id:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.role != MessageRole.assistant:
        raise HTTPException(
            status_code=400,
            detail="Feedback can only be set on assistant messages",
        )

    message.feedback = MessageFeedback(body.feedback.value)
    message.ideal_answer = body.ideal_answer if body.ideal_answer else None
    db.commit()
    db.refresh(message)

    return MessageFeedbackResponse(
        id=message.id,
        feedback=body.feedback,
        ideal_answer=message.ideal_answer,
    )


@chat_router.get("/bad-answers", response_model=BadAnswerListResponse)
def list_bad_answers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BadAnswerListResponse:
    """
    List assistant messages with feedback=down for the authenticated client.
    JWT auth required. Returns question (previous user msg), answer, ideal_answer.
    """
    from sqlalchemy.orm import joinedload

    # N+1 fix: single query loads all client messages; prev user found in-memory (no per-message DB query)
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    all_messages = (
        db.query(Message)
        .join(Chat, Message.chat_id == Chat.id)
        .options(joinedload(Message.chat))
        .filter(Chat.client_id == client.id)
        .order_by(Message.chat_id, Message.created_at)
        .all()
    )

    messages_by_chat: dict[uuid.UUID, list[Message]] = defaultdict(list)
    for msg in all_messages:
        messages_by_chat[msg.chat_id].append(msg)

    bad_answers: list[BadAnswerItem] = []
    for chat_id, messages in messages_by_chat.items():
        for i, msg in enumerate(messages):
            if msg.role == MessageRole.assistant and msg.feedback == MessageFeedback.down:
                prev_user = None
                for j in range(i - 1, -1, -1):
                    if messages[j].role == MessageRole.user:
                        prev_user = messages[j]
                        break
                question = prev_user.content if prev_user else None
                chat = msg.chat
                bad_answers.append(
                    BadAnswerItem(
                        message_id=msg.id,
                        session_id=chat.session_id,
                        question=question,
                        answer=msg.content,
                        ideal_answer=msg.ideal_answer,
                        created_at=msg.created_at,
                    )
                )

    bad_answers.sort(key=lambda x: x.created_at, reverse=True)
    items = bad_answers[offset : offset + limit]

    return BadAnswerListResponse(items=items)


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
