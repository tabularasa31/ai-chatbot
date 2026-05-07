"""FastAPI chat endpoints."""

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from openai import APIError, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.auth.middleware import require_admin_user, require_verified_user
from backend.chat.events import _emit_chat_feedback_event
from backend.chat.history_service import (
    delete_session_original_content,
    get_chat_history,
    get_session_logs,
    list_chat_sessions,
)
from backend.chat.language import detect_language, localize_text_to_language_result
from backend.chat.llm_unavailable import LlmFailureType, classify_llm_failure
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
    async_process_chat_message,
    record_gap_feedback_for_message,
)
from backend.core.db import get_async_db, get_db, run_sync
from backend.core.idempotency import idempotent_section
from backend.core.limiter import limiter
from backend.core.openai_client import is_quota_exceeded
from backend.escalation.schemas import ManualEscalateRequest, ManualEscalateResponse
from backend.escalation.service import perform_manual_escalation
from backend.models import (
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
from backend.tenants.llm_alerts import (
    apply_clear_alert,
    apply_llm_failure,
)
from backend.tenants.llm_alerts import (
    is_actionable as is_actionable_llm_failure,
)
from backend.tenants.service import get_tenant_by_api_key, get_tenant_by_user

logger = logging.getLogger(__name__)


def _notify_quota_exceeded(tenant: "Tenant", db: "Session", lang: str = "en", api_key: str | None = None) -> str:
    """Log the quota-exceeded event to Sentry and return the user-facing
    error detail string (includes support email if known).

    Tenant alert state + email are raised separately via ``apply_llm_failure``
    in a ``to_thread`` (off the event loop) — see the chat handler below.
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


chat_router = APIRouter(tags=["chat"])


def _require_original_access(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Original content access requires admin privileges")


@chat_router.post("", response_model=ChatTurnResponse)
@limiter.limit("30/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_browser_locale: Annotated[str | None, Header(alias="X-Browser-Locale")] = None,
) -> ChatTurnResponse | JSONResponse:
    """
    Chat endpoint (PUBLIC — no JWT, uses X-API-Key).

    Returns RAG-generated answer with source documents.
    Errors: 401 (invalid/missing API key), 422 (invalid question), 503 (OpenAI unavailable).

    Honors the optional `Idempotency-Key` header: a retry within 24h replays
    the original response instead of re-running the LLM pipeline.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    tenant = await run_sync(db, lambda s: get_tenant_by_api_key(x_api_key, s))
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not tenant.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    session_id = body.session_id or uuid.uuid4()

    async with idempotent_section(
        request, tenant_id=str(tenant.id), scope="chat"
    ) as section:
        if section.cached is not None:
            return JSONResponse(
                status_code=section.cached.status_code, content=section.cached.body
            )

        try:
            outcome = await async_process_chat_message(
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
                detail = await run_sync(
                    db,
                    lambda s: _notify_quota_exceeded(tenant, s, lang=lang, api_key=tenant.openai_api_key),
                )
                # Raise the tenant-level alert + throttled email off the
                # event loop (sync httpx + DB inside).
                await asyncio.to_thread(
                    apply_llm_failure, tenant.id, LlmFailureType.quota_exhausted
                )
                raise HTTPException(status_code=402, detail=detail) from None
            raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
        except APIError as exc:
            # Classify so an invalid/revoked key surfaces a tenant-level alert
            # the same way quota_exhausted does (RateLimitError path above).
            failure_state = classify_llm_failure(exc)
            if is_actionable_llm_failure(failure_state.type):
                await asyncio.to_thread(
                    apply_llm_failure, tenant.id, failure_state.type
                )
            raise HTTPException(
                status_code=503,
                detail="OpenAI service unavailable",
            ) from None

        # Clear any active alert only when the LLM actually ran successfully
        # (tokens_used > 0). Greetings / cached / canned outcomes don't
        # exercise the provider, so a "successful" turn there isn't evidence
        # the broken key is back. Skip the DB roundtrip entirely when we
        # know there's no alert to clear.
        if outcome.tokens_used > 0 and tenant.llm_alert_type is not None:
            await asyncio.to_thread(apply_clear_alert, tenant.id)

        response = ChatTurnResponse(
            text=outcome.text,
            session_id=session_id,
            source_documents=outcome.document_ids,
            tokens_used=outcome.tokens_used,
            chat_ended=outcome.chat_ended,
            ticket_number=outcome.ticket_number,
        )
        if section.active:
            payload = response.model_dump(mode="json")
            await section.record(status_code=200, body=payload)
            return JSONResponse(status_code=200, content=payload)
        return response


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
    trig = {
        "user_request": EscalationTrigger.user_request,
        "answer_rejected": EscalationTrigger.answer_rejected,
        "llm_unavailable": EscalationTrigger.llm_unavailable,
    }[body.trigger]
    try:
        msg, tnum = perform_manual_escalation(
            db,
            tenant,
            session_id,
            api_key=tenant.openai_api_key,
            user_note=body.user_note,
            trigger=trig,
            failure_type=body.failure_type,
            original_user_message=body.original_user_message,
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


@chat_router.post("/logs/session/{session_id}/delete-original", response_model=DeletedCountResponse, include_in_schema=False)
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

    if body.feedback.value in ("up", "down"):
        _emit_chat_feedback_event(
            tenant_public_id=getattr(tenant, "public_id", None),
            bot_public_id=None,
            distinct_id=str(current_user.id),
            feedback="positive" if body.feedback.value == "up" else "negative",
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
