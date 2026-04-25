"""FastAPI chat endpoints."""

import logging
import uuid
from collections import defaultdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from openai import APIError, RateLimitError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.middleware import require_admin_user, require_verified_user
from backend.chat.language import detect_language, localize_text_to_language_result
from backend.chat.schemas import (
    BadAnswerItem,
    BadAnswerListResponse,
    ChatHistoryResponse,
    ChatMessageLogItem,
    ChatMessageLogResponse,
    ChatRequest,
    ChatSessionListResponse,
    ChatSessionSummaryResponse,
    ChatTurnResponse,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    MessageResponse,
)
from backend.chat.service import (
    delete_session_original_content,
    get_chat_history,
    get_session_logs,
    list_chat_sessions,
    process_chat_message,
    record_gap_feedback_for_message,
    run_debug,
)
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.core.openai_client import is_quota_exceeded
from backend.email.service import send_email
from backend.escalation.schemas import ManualEscalateRequest, ManualEscalateResponse
from backend.escalation.service import perform_manual_escalation
from backend.models import (
    Bot,
    Chat,
    EscalationTrigger,
    Message,
    MessageFeedback,
    MessageRole,
    PiiEvent,
    PiiEventDirection,
    Tenant,
    TenantProfile,
    User,
)
from backend.privacy_schemas import DeletedCountResponse
from backend.tenants.service import get_tenant_by_api_key, get_tenant_by_user

logger = logging.getLogger(__name__)


def _notify_quota_exceeded(tenant: "Tenant", db: "Session", lang: str = "en", api_key: str | None = None) -> str:
    """Log OpenAI quota-exceeded event to Sentry and email the tenant admin once.

    Returns the user-facing error detail string (includes support email if known).
    """
    logger.error(
        "openai_quota_exceeded: tenant_id=%s tenant_name=%s",
        tenant.id,
        tenant.name,
    )
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("error_kind", "openai_quota_exceeded")
            scope.set_context("tenant", {"tenant_id": str(tenant.id), "tenant_name": tenant.name})
            sentry_sdk.capture_message(
                f"OpenAI quota exceeded for tenant '{tenant.name}'",
                level="error",
                scope=scope,
            )
    except Exception:
        pass

    profile = db.get(TenantProfile, tenant.id)
    support_email: str | None = profile.support_email if profile else None

    admin = (
        db.query(User)
        .filter(User.tenant_id == tenant.id, User.is_admin.is_(True))
        .first()
    )
    if admin:
        try:
            send_email(
                to=admin.email,
                subject="[Chat9] OpenAI quota exceeded — action required",
                body=(
                    "Hello,\n\n"
                    "Your OpenAI API key has run out of credits. "
                    "Chat9 cannot generate responses for your users until you top up your balance.\n\n"
                    "Please visit https://platform.openai.com/settings/organization/billing "
                    "to add credits.\n\n"
                    "— Chat9"
                ),
            )
        except Exception:
            logger.warning("quota_exceeded_email_failed: tenant_id=%s", tenant.id)

    contact = f" at {support_email}" if support_email else ""
    canonical = (
        "We're currently experiencing technical difficulties and are unable to respond via chat. "
        f"We apologize for the inconvenience — please contact our support team{contact} by email."
    )
    return localize_text_to_language_result(
        canonical_text=canonical,
        target_language=lang,
        api_key=api_key,
    ).text


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
    best_rank_score: float | None = None
    best_confidence_score: float | None = None
    confidence_source: Literal["vector_similarity", "rank_score", "none"] | None = None
    contradiction_detected: bool = False
    contradiction_count: int = 0
    contradiction_pair_count: int = 0
    contradiction_basis_types: list[str] = Field(default_factory=list)
    contradiction_adjudication_enabled: bool = False
    contradiction_adjudication_applied_to_any_fact: bool = False
    contradiction_adjudication_status: str = "disabled"
    contradiction_adjudication_candidate_count: int = 0
    contradiction_adjudication_sent_count: int = 0
    contradiction_adjudication_completed_count: int = 0
    contradiction_adjudication_confirmed_count: int = 0
    contradiction_adjudication_rejected_count: int = 0
    contradiction_adjudication_inconclusive_count: int = 0
    contradiction_adjudication_error_count: int = 0
    reliability: dict | None = None
    chunks: list[DebugChunkResponse]
    validation: dict | None = None
    # Pipeline decision fields (added alongside retrieval info)
    strategy: Literal["faq_direct", "faq_context", "rag_only", "guard_reject"] | None = None
    reject_reason: Literal["injection", "not_relevant", "low_retrieval", "insufficient_confidence"] | None = None
    is_reject: bool = False
    is_faq_direct: bool = False
    validation_applied: bool = False
    validation_outcome: Literal["valid", "fallback"] | None = None


class ChatDebugResponse(BaseModel):
    """Response from chat debug endpoint."""

    answer: str
    raw_answer: str | None = None
    tokens_used: int
    debug: DebugInfoResponse

chat_router = APIRouter(tags=["chat"])


def _resolve_debug_client(
    *,
    db: Session,
    current_user: User,
    bot_id: str,
) -> Tenant:
    row = (
        db.query(Bot, Tenant)
        .join(Tenant, Bot.tenant_id == Tenant.id)
        .filter(Bot.public_id == bot_id, Tenant.id == current_user.tenant_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Bot not found")
    return row[1]


def _require_original_access(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Original content access requires admin privileges")


@chat_router.post("", response_model=ChatTurnResponse)
@limiter.limit("30/minute")
def chat(
    request: Request,
    body: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_browser_locale: Annotated[str | None, Header(alias="X-Browser-Locale")] = None,
) -> ChatTurnResponse:
    """
    Chat endpoint (PUBLIC — no JWT, uses X-API-Key).

    Returns RAG-generated answer with source documents.
    Errors: 401 (invalid/missing API key), 422 (invalid question), 503 (OpenAI unavailable).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    tenant = get_tenant_by_api_key(x_api_key, db)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not tenant.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    session_id = body.session_id or uuid.uuid4()

    try:
        outcome = process_chat_message(
            tenant_id=tenant.id,
            question=body.question,
            session_id=session_id,
            db=db,
            api_key=tenant.openai_api_key,
            browser_locale=x_browser_locale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RateLimitError as exc:
        if is_quota_exceeded(exc):
            lang = detect_language(body.question).detected_language
            raise HTTPException(
                status_code=402,
                detail=_notify_quota_exceeded(tenant, db, lang=lang, api_key=tenant.openai_api_key),
            ) from None
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        ) from None

    return ChatTurnResponse(
        text=outcome.text,
        session_id=session_id,
        source_documents=outcome.document_ids,
        tokens_used=outcome.tokens_used,
        chat_ended=outcome.chat_ended,
        ticket_number=outcome.ticket_number,
    )


@chat_router.post("/debug", response_model=ChatDebugResponse)
@limiter.limit("30/minute")
def chat_debug(
    request: Request,
    body: DebugRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Annotated[str, Query(min_length=1)],
) -> ChatDebugResponse:
    """
    Debug endpoint: run RAG pipeline without persisting to DB.
    JWT auth required. Returns answer + retrieval debug info.
    """
    tenant = _resolve_debug_client(db=db, current_user=current_user, bot_id=bot_id)
    if not tenant.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    try:
        answer, tokens_used, debug_dict = run_debug(
            tenant_id=tenant.id,
            question=body.question,
            db=db,
            api_key=tenant.openai_api_key,
        )
    except RateLimitError as exc:
        if is_quota_exceeded(exc):
            lang = detect_language(body.question).detected_language
            raise HTTPException(
                status_code=402,
                detail=_notify_quota_exceeded(tenant, db, lang=lang, api_key=tenant.openai_api_key),
            ) from None
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        ) from None

    debug_resp = DebugInfoResponse(
        mode=debug_dict["mode"],
        best_rank_score=debug_dict.get("best_rank_score"),
        best_confidence_score=debug_dict.get("best_confidence_score"),
        confidence_source=debug_dict.get("confidence_source"),
        contradiction_detected=bool(debug_dict.get("contradiction_detected", False)),
        contradiction_count=int(debug_dict.get("contradiction_count", 0)),
        contradiction_pair_count=int(debug_dict.get("contradiction_pair_count", 0)),
        contradiction_basis_types=list(debug_dict.get("contradiction_basis_types", [])),
        contradiction_adjudication_enabled=bool(
            debug_dict.get("contradiction_adjudication_enabled", False)
        ),
        contradiction_adjudication_applied_to_any_fact=bool(
            debug_dict.get("contradiction_adjudication_applied_to_any_fact", False)
        ),
        contradiction_adjudication_status=str(
            debug_dict.get("contradiction_adjudication_status", "disabled")
        ),
        contradiction_adjudication_candidate_count=int(
            debug_dict.get("contradiction_adjudication_candidate_count", 0)
        ),
        contradiction_adjudication_sent_count=int(
            debug_dict.get("contradiction_adjudication_sent_count", 0)
        ),
        contradiction_adjudication_completed_count=int(
            debug_dict.get("contradiction_adjudication_completed_count", 0)
        ),
        contradiction_adjudication_confirmed_count=int(
            debug_dict.get("contradiction_adjudication_confirmed_count", 0)
        ),
        contradiction_adjudication_rejected_count=int(
            debug_dict.get("contradiction_adjudication_rejected_count", 0)
        ),
        contradiction_adjudication_inconclusive_count=int(
            debug_dict.get("contradiction_adjudication_inconclusive_count", 0)
        ),
        contradiction_adjudication_error_count=int(
            debug_dict.get("contradiction_adjudication_error_count", 0)
        ),
        reliability=debug_dict.get("reliability"),
        chunks=[
            DebugChunkResponse(
                document_id=c["document_id"],
                score=c["score"],
                preview=c["preview"],
            )
            for c in debug_dict["chunks"]
        ],
        validation=debug_dict.get("validation"),
        strategy=debug_dict.get("strategy"),
        reject_reason=debug_dict.get("reject_reason"),
        is_reject=bool(debug_dict.get("is_reject", False)),
        is_faq_direct=bool(debug_dict.get("is_faq_direct", False)),
        validation_applied=bool(debug_dict.get("validation_applied", False)),
        validation_outcome=debug_dict.get("validation_outcome"),
    )
    return ChatDebugResponse(
        answer=answer,
        raw_answer=debug_dict.get("raw_answer"),
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
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ManualEscalateResponse:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    tenant = get_tenant_by_api_key(x_api_key, db)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not tenant.openai_api_key:
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
            tenant,
            session_id,
            api_key=tenant.openai_api_key,
            user_note=body.user_note,
            trigger=trig,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    except RateLimitError as exc:
        if is_quota_exceeded(exc):
            lang = detect_language(body.user_note).detected_language if body.user_note else "en"
            raise HTTPException(
                status_code=402,
                detail=_notify_quota_exceeded(tenant, db, lang=lang, api_key=tenant.openai_api_key),
            ) from None
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    except APIError:
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    return ManualEscalateResponse(message=msg, ticket_number=tnum)


@chat_router.get("/sessions", response_model=ChatSessionListResponse)
def get_sessions(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatSessionListResponse:
    """
    List all chat sessions for the authenticated tenant (inbox-style).
    JWT auth required. Returns sessions sorted by last_activity DESC.
    """
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    summaries = list_chat_sessions(tenant.id, db)
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
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    include_original: bool = Query(False),
) -> ChatMessageLogResponse:
    """
    Get full message log for a session (read-only).
    JWT auth required. Returns 404 if session not found or not owner.
    """
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if include_original:
        _require_original_access(current_user)

    logs = get_session_logs(session_id, tenant.id, db, include_original=include_original)
    if logs is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if include_original:
        for msg_id, _sid, _role, _content, content_original, content_original_available, _feedback, _ideal_answer, _created_at in logs:
            if not content_original_available or content_original is None:
                continue
            db.add(
                PiiEvent(
                    tenant_id=tenant.id,
                    chat_id=None,
                    message_id=msg_id,
                    actor_user_id=current_user.id,
                    direction=PiiEventDirection.original_view,
                    entity_type="ORIGINAL_VIEW",
                    count=1,
                    action_path=f"/chat/logs/session/{session_id}",
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


@chat_router.post("/logs/session/{session_id}/delete-original", response_model=DeletedCountResponse)
def delete_session_original_route(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeletedCountResponse:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    chat, deleted_count = delete_session_original_content(session_id, tenant.id, db)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")
    if deleted_count:
        db.add(
            PiiEvent(
                tenant_id=tenant.id,
                chat_id=chat.id,
                message_id=None,
                actor_user_id=current_user.id,
                direction=PiiEventDirection.original_delete,
                entity_type="ORIGINAL_DELETE",
                count=deleted_count,
                action_path=f"/chat/logs/session/{session_id}/delete-original",
            )
        )
        db.commit()
        db.refresh(chat)
    return DeletedCountResponse(deleted_count=deleted_count)


@chat_router.post("/messages/{message_id}/feedback", response_model=MessageFeedbackResponse)
def set_message_feedback(
    message_id: uuid.UUID,
    body: MessageFeedbackRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MessageFeedbackResponse:
    """
    Set feedback (up/down) and optional ideal_answer on an assistant message.
    JWT auth required. 400 if message is not assistant, 404 if not found or not owner.
    """
    from sqlalchemy.orm import joinedload

    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    message = (
        db.query(Message)
        .options(joinedload(Message.chat))
        .filter(Message.id == message_id)
        .first()
    )
    if not message or message.chat.tenant_id != tenant.id:
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
    try:
        record_gap_feedback_for_message(
            db=db,
            tenant_id=tenant.id,
            assistant_message_id=message.id,
            feedback_value=body.feedback.value,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "gap_analyzer_feedback_sync_failed: tenant_id=%s assistant_message_id=%s feedback=%s",
            tenant.id,
            message.id,
            body.feedback.value,
            exc_info=True,
        )

    return MessageFeedbackResponse(
        id=message.id,
        feedback=body.feedback,
        ideal_answer=message.ideal_answer,
    )


@chat_router.get("/bad-answers", response_model=BadAnswerListResponse)
def list_bad_answers(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BadAnswerListResponse:
    """
    List assistant messages with feedback=down for the authenticated tenant.
    JWT auth required. Returns question (previous user msg), answer, ideal_answer.
    """
    from sqlalchemy.orm import joinedload

    # N+1 fix: single query loads all tenant messages; prev user found in-memory (no per-message DB query)
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    all_messages = (
        db.query(Message)
        .join(Chat, Message.chat_id == Chat.id)
        .options(joinedload(Message.chat))
        .filter(Chat.tenant_id == tenant.id)
        .order_by(Message.chat_id, Message.created_at)
        .all()
    )

    messages_by_chat: dict[uuid.UUID, list[Message]] = defaultdict(list)
    for msg in all_messages:
        messages_by_chat[msg.chat_id].append(msg)

    bad_answers: list[BadAnswerItem] = []
    for _, messages in messages_by_chat.items():
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
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatHistoryResponse:
    """
    Get chat history for a session (protected JWT).

    Returns 404 if session not found or not owner.
    """
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    messages = get_chat_history(session_id, tenant.id, db)
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
