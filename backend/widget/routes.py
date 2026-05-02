"""Widget API routes for embedded chat (public, bot-id based)."""

import asyncio
import json
import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openai import APIError
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.chat.handlers.rag import _CitationStreamFilter
from backend.chat.language import localize_text_to_language_result
from backend.chat.schemas import WidgetChatTurnResponse
from backend.chat.service import async_process_chat_message
from backend.contact_sessions.service import start_user_session
from backend.core import db as core_db
from backend.core.config import settings
from backend.core.db import get_db
from backend.core.limiter import (
    limiter,
    widget_bot_rate_limit_key,
    widget_init_rate_limit_key,
    widget_public_rate_limit_key,
)
from backend.core.security import validate_kyc_token_detail
from backend.escalation.schemas import ManualEscalateRequest, ManualEscalateResponse
from backend.escalation.service import perform_manual_escalation
from backend.models import (
    Chat,
    Document,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    Tenant,
    UserContext,
)
from backend.tenants.service import (
    get_kyc_decrypted_keys_for_validation,
)
from backend.tenants.widget_chat_gate import (
    WidgetChatTenantGateError,
    get_bot_and_tenant_for_widget_chat,
    get_bot_and_tenant_for_widget_session,
)
from backend.widget.service import (
    SESSION_CLOSED_CODE,
    SESSION_INVALID_CODE,
    SESSION_NOT_FOUND_CODE,
    apply_identity_context_patch,
    sanitize_locale,
    widget_session_error_detail,
)

logger = logging.getLogger(__name__)

widget_router = APIRouter(prefix="/widget", tags=["widget"])
_WIDGET_MESSAGE_MAX_CHARS = settings.widget_message_max_chars


class WidgetSessionInitRequest(BaseModel):
    bot_id: str = Field(..., min_length=1)
    identity_token: str | None = None
    locale: str | None = Field(default=None, max_length=64)


class WidgetSessionInitResponse(BaseModel):
    session_id: uuid.UUID
    mode: Literal["identified", "anonymous"]


class WidgetChatRequest(BaseModel):
    message: str | None = None
    locale: str | None = Field(default=None, max_length=64)


class WidgetLinkSafetyLabels(BaseModel):
    title: str
    body: str
    continue_label: str
    cancel_label: str


class WidgetConfigResponse(BaseModel):
    link_safety_enabled: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    link_safety_labels: WidgetLinkSafetyLabels


def _default_link_safety_labels() -> WidgetLinkSafetyLabels:
    return WidgetLinkSafetyLabels(
        title="Open external link?",
        body="You are going to {hostname}. Continue?",
        continue_label="Open",
        cancel_label="Cancel",
    )


@widget_router.get("/health")
def widget_health() -> dict[str, str]:
    """Health check for widget endpoints."""
    return {"status": "ok"}


def _link_safety_labels(
    locale: str | None,
    *,
    encrypted_api_key: str | None,
    tenant_id: str,
    bot_id: str,
) -> WidgetLinkSafetyLabels:
    target_language = sanitize_locale(locale)
    labels = _default_link_safety_labels()
    if not target_language:
        return labels

    return WidgetLinkSafetyLabels(
        title=localize_text_to_language_result(
            canonical_text=labels.title,
            target_language=target_language,
            api_key=encrypted_api_key,
            fallback_locale=target_language,
            operation="widget_link_safety_localize",
            tenant_id=tenant_id,
            bot_id=bot_id,
        ).text,
        body=localize_text_to_language_result(
            canonical_text=labels.body,
            target_language=target_language,
            api_key=encrypted_api_key,
            fallback_locale=target_language,
            operation="widget_link_safety_localize",
            tenant_id=tenant_id,
            bot_id=bot_id,
        ).text,
        continue_label=localize_text_to_language_result(
            canonical_text=labels.continue_label,
            target_language=target_language,
            api_key=encrypted_api_key,
            fallback_locale=target_language,
            operation="widget_link_safety_localize",
            tenant_id=tenant_id,
            bot_id=bot_id,
        ).text,
        cancel_label=localize_text_to_language_result(
            canonical_text=labels.cancel_label,
            target_language=target_language,
            api_key=encrypted_api_key,
            fallback_locale=target_language,
            operation="widget_link_safety_localize",
            tenant_id=tenant_id,
            bot_id=bot_id,
        ).text,
    )


@widget_router.get("/config", response_model=WidgetConfigResponse)
@limiter.limit("30/minute", key_func=widget_public_rate_limit_key)
def widget_config(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    locale: Annotated[str | None, Query(description="Browser locale hint (e.g. ru-RU)")] = None,
    db: Session = Depends(get_db),
) -> WidgetConfigResponse:
    try:
        bot, tenant = get_bot_and_tenant_for_widget_chat(db, bot_id)
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        if e.reason == WidgetChatTenantGateError.INACTIVE:
            raise HTTPException(status_code=403, detail="Tenant is not active") from e
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        ) from e

    allowed_domains = bot.allowed_domains if isinstance(bot.allowed_domains, list) else []
    labels = (
        _link_safety_labels(
            locale,
            encrypted_api_key=tenant.openai_api_key,
            tenant_id=str(tenant.id),
            bot_id=bot.public_id,
        )
        if bot.link_safety_enabled
        else _default_link_safety_labels()
    )
    return WidgetConfigResponse(
        link_safety_enabled=bool(bot.link_safety_enabled),
        allowed_domains=[str(domain) for domain in allowed_domains if str(domain).strip()],
        link_safety_labels=labels,
    )


def _resolve_widget_identity(
    tenant: Tenant,
    identity_token: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Returns (stored_user_context dict, None) on success, or (None, failure_reason).
    Never logs PII.
    """
    if not identity_token or not identity_token.strip():
        return None, None
    keys = get_kyc_decrypted_keys_for_validation(tenant)
    if not keys:
        return None, "no_secret_configured"
    last_reason = "bad_signature"
    for sk, _label in keys:
        raw_ctx, err = validate_kyc_token_detail(
            identity_token.strip(), sk
        )
        if raw_ctx is not None:
            try:
                validated = UserContext.model_validate(raw_ctx)
                return validated.model_dump(), None
            except Exception:
                return None, "malformed_context"
        if err:
            last_reason = err
    return None, last_reason


@widget_router.post("/session/init", response_model=WidgetSessionInitResponse)
@limiter.limit("10/minute", key_func=widget_init_rate_limit_key)
def widget_session_init(
    request: Request,
    body: Annotated[WidgetSessionInitRequest, Body()],
    db: Session = Depends(get_db),
) -> WidgetSessionInitResponse:
    """
    Start a widget session. Optional signed identity_token enables identified mode.
    """
    try:
        _bot, tenant = get_bot_and_tenant_for_widget_session(db, body.bot_id)
    except WidgetChatTenantGateError as e:
        logger.info("widget_session_init_rejected", extra={"reason": e.reason})
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        if e.reason == WidgetChatTenantGateError.INACTIVE:
            raise HTTPException(status_code=403, detail="Tenant is not active") from e
        raise HTTPException(status_code=400, detail="Bot not available") from e

    session_id = uuid.uuid4()
    mode: Literal["identified", "anonymous"] = "anonymous"
    locale = sanitize_locale(body.locale)

    ctx, fail_reason = _resolve_widget_identity(tenant, body.identity_token)
    if ctx is not None:
        merged = apply_identity_context_patch(
            {"user_id": ctx["user_id"]},
            ctx,
            browser_locale=locale,
        )
        chat = Chat(
            tenant_id=tenant.id,
            session_id=session_id,
            user_context=merged,
        )
        db.add(chat)
        db.flush()
        start_user_session(
            db,
            tenant_id=tenant.id,
            user_context=merged,
            started_at=chat.created_at,
        )
        db.commit()
        logger.info("kyc_session_created: tenant_id=%s", tenant.id)
        mode = "identified"
    elif body.identity_token and body.identity_token.strip():
        reason = fail_reason or "invalid_token"
        logger.info("kyc_validation_failed: reason=%s", reason)
    elif locale is not None:
        chat = Chat(
            tenant_id=tenant.id,
            session_id=session_id,
            user_context={"browser_locale": locale},
        )
        db.add(chat)
        db.commit()

    return WidgetSessionInitResponse(session_id=session_id, mode=mode)


@widget_router.post(
    "/chat",
    responses={
        200: {
            "description": (
                    "Server-sent events stream (`text/event-stream`). Each frame is "
                    "`type: 'chunk'` (incremental text) or `type: 'done'`; the `done` "
                    "frame's payload conforms to `WidgetChatTurnResponse`."
                ),
                "content": {
                    "text/event-stream": {
                        "schema": {"$ref": "#/components/schemas/WidgetChatTurnResponse"},
                    },
                },
            }
    },
)
@limiter.limit(
    settings.effective_widget_chat_per_client_rate,
    key_func=widget_bot_rate_limit_key,
)
@limiter.limit("30/minute", key_func=widget_public_rate_limit_key)
def widget_chat(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    body: Annotated[WidgetChatRequest | None, Body()] = None,
    session_id: Annotated[str | None, Query(description="Optional session ID")] = None,
    locale: Annotated[
        str | None, Query(description="Browser locale hint (e.g. ru-RU)")
    ] = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    PUBLIC endpoint for embedded widget.
    No authentication required (bot public_id = permission).
    """
    resolved_message = body.message if body is not None else None
    if resolved_message is not None:
        resolved_message = resolved_message.strip()

    locale_hint = sanitize_locale((body.locale if body is not None else None) or locale)

    try:
        _bot, tenant = get_bot_and_tenant_for_widget_chat(db, bot_id)
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        if e.reason == WidgetChatTenantGateError.INACTIVE:
            raise HTTPException(status_code=403, detail="Tenant is not active") from e
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        ) from e

    existing_chat: Chat | None = None
    if session_id:
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail=widget_session_error_detail(
                    SESSION_INVALID_CODE,
                    "Invalid session_id",
                ),
            ) from None
        existing_chat = (
            db.query(Chat)
            .filter(
                Chat.tenant_id == tenant.id,
                Chat.session_id == sid,
                or_(Chat.bot_id == _bot.id, Chat.bot_id.is_(None)),
            )
            .first()
        )
        if existing_chat is None:
            raise HTTPException(
                status_code=409,
                detail=widget_session_error_detail(
                    SESSION_NOT_FOUND_CODE,
                    "Session not found",
                ),
            )
        if existing_chat.bot_id is None:
            existing_chat.bot_id = _bot.id
            db.add(existing_chat)
            db.commit()
        if existing_chat.ended_at is not None:
            raise HTTPException(
                status_code=409,
                detail=widget_session_error_detail(
                    SESSION_CLOSED_CODE,
                    "Session is closed",
                ),
            )
    else:
        sid = uuid.uuid4()

    if not resolved_message:
        if session_id:
            logger.info(
                "widget_message_rejected",
                extra={"reason": "empty", "length": 0},
            )
            raise HTTPException(
                status_code=422,
                detail={"code": "message_required", "message": "message is required"},
            )
        resolved_message = ""
    elif len(resolved_message) > _WIDGET_MESSAGE_MAX_CHARS:
        logger.info(
            "widget_message_rejected",
            extra={"reason": "too_long", "length": len(resolved_message)},
        )
        raise HTTPException(
            status_code=413,
            detail={
                "code": "message_too_long",
                "max_chars": _WIDGET_MESSAGE_MAX_CHARS,
            },
        )

    process_kwargs = dict(
        tenant_id=tenant.id,
        question=resolved_message,
        session_id=sid,
        api_key=tenant.openai_api_key,
        user_context=None,
        browser_locale=locale_hint,
        disclosure_config=_bot.disclosure_config if isinstance(_bot.disclosure_config, dict) else None,
        bot_id=_bot.id,
        bot_public_id=getattr(_bot, "public_id", None),
    )

    return _widget_chat_stream(sid, process_kwargs)


_STREAM_SENTINEL = object()


def _widget_chat_stream(
    sid: uuid.UUID,
    process_kwargs: dict,
) -> StreamingResponse:
    async def event_stream():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Any] = asyncio.Queue()
        result_holder: dict[str, Any] = {}

        # The async pipeline delegates LLM generation to ``asyncio.to_thread``,
        # so ``stream_callback`` fires from a worker thread. Bridge each token
        # back onto the running loop via ``call_soon_threadsafe``.
        _citation_filter = _CitationStreamFilter(
            lambda t: loop.call_soon_threadsafe(q.put_nowait, ("chunk", t))
        )

        def on_chunk(text: str) -> None:
            if text:
                _citation_filter.feed(text)

        def on_status(stage: str) -> None:
            if stage:
                loop.call_soon_threadsafe(q.put_nowait, ("status", stage))

        async def run_pipeline() -> None:
            try:
                async with core_db.AsyncSessionLocal() as worker_db:
                    outcome = await async_process_chat_message(
                        db=worker_db,
                        stream_callback=on_chunk,
                        status_callback=on_status,
                        **process_kwargs,
                    )
                    result_holder["outcome"] = outcome
                    if outcome and outcome.document_ids:
                        try:
                            res = await worker_db.execute(
                                select(Document.filename, Document.source_url).where(
                                    Document.id.in_(outcome.document_ids)
                                )
                            )
                            seen: dict[str, str] = {}
                            for filename, source_url in res.all():
                                if source_url and source_url not in seen:
                                    seen[source_url] = filename
                            result_holder["sources"] = [
                                {"title": title, "url": url}
                                for url, title in seen.items()
                            ]
                        except Exception:
                            logger.warning("widget_source_lookup_failed", exc_info=True)
            except BaseException as exc:
                result_holder["error"] = exc
            finally:
                _citation_filter.finish()
                # Drain pending call_soon_threadsafe puts so the final flushed
                # chunk lands before the sentinel.
                await asyncio.sleep(0)
                q.put_nowait(_STREAM_SENTINEL)

        # Initial "thinking" status so the client shows a meaningful label
        # immediately, before guards and retrieval start producing signals.
        yield f"data: {json.dumps({'type': 'status', 'stage': 'thinking'})}\n\n"

        task = asyncio.create_task(run_pipeline())
        streamed_any = False
        try:
            while True:
                item = await q.get()
                if item is _STREAM_SENTINEL:
                    break
                kind, text = item
                if kind == "chunk":
                    streamed_any = True
                    yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
                elif kind == "status":
                    yield f"data: {json.dumps({'type': 'status', 'stage': text})}\n\n"
        except BaseException:
            # Client disconnected or generator was closed — cancel the worker
            # so it doesn't keep running detached.
            task.cancel()
            raise

        await task

        err = result_holder.get("error")
        if err is not None:
            if isinstance(err, ValueError):
                payload = {"type": "error", "code": 422, "message": str(err)}
            elif isinstance(err, APIError):
                payload = {"type": "error", "code": 503, "message": "OpenAI service unavailable"}
            else:
                logger.exception("widget_chat_stream_failed", exc_info=err)
                payload = {"type": "error", "code": 500, "message": "Internal error"}
            yield f"data: {json.dumps(payload)}\n\n"
            return

        outcome = result_holder.get("outcome")
        final_text = outcome.text if outcome is not None else ""
        if not streamed_any and final_text:
            yield f"data: {json.dumps({'type': 'chunk', 'text': final_text})}\n\n"
        turn_response = WidgetChatTurnResponse(
            text=final_text,
            session_id=sid,
            chat_ended=bool(outcome.chat_ended) if outcome is not None else False,
            ticket_number=outcome.ticket_number if outcome is not None else None,
        )
        done_payload: dict[str, Any] = {
            "type": "done",
            **turn_response.model_dump(exclude_none=True, mode="json"),
        }
        sources = result_holder.get("sources")
        if sources:
            done_payload["sources"] = sources
        yield f"data: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class WidgetHistoryMessage(BaseModel):
    role: str
    content: str


class WidgetHistoryResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[WidgetHistoryMessage]
    chat_ended: bool
    ticket_number: str | None = None


@widget_router.get("/history", response_model=WidgetHistoryResponse)
@limiter.limit("30/minute", key_func=widget_public_rate_limit_key)
def widget_history(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    session_id: Annotated[str, Query(description="Chat session UUID")],
    db: Session = Depends(get_db),
) -> WidgetHistoryResponse:
    """Return message history for a widget session (public, no auth)."""
    try:
        _bot, tenant = get_bot_and_tenant_for_widget_chat(db, bot_id)
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        raise HTTPException(status_code=400, detail="Bot not available") from e

    try:
        sid = uuid.UUID(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid session_id") from None

    chat = (
        db.query(Chat)
        .filter(
            Chat.tenant_id == tenant.id,
            Chat.session_id == sid,
            or_(Chat.bot_id == _bot.id, Chat.bot_id.is_(None)),
        )
        .first()
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = (
        db.query(Message)
        .filter(
            Message.chat_id == chat.id,
            Message.role.in_([MessageRole.user, MessageRole.assistant]),
        )
        .order_by(Message.created_at.asc())
        .all()
    )

    ticket_number: str | None = None
    if chat.escalation_awaiting_ticket_id is not None:
        ticket = db.get(EscalationTicket, chat.escalation_awaiting_ticket_id)
        if ticket is not None:
            ticket_number = ticket.ticket_number

    return WidgetHistoryResponse(
        session_id=sid,
        messages=[
            WidgetHistoryMessage(role=m.role.value, content=m.content)
            for m in messages
        ],
        chat_ended=chat.ended_at is not None,
        ticket_number=ticket_number,
    )


@widget_router.post("/escalate", response_model=ManualEscalateResponse)
@limiter.limit("20/minute", key_func=widget_public_rate_limit_key)
def widget_escalate(
    request: Request,
    body: ManualEscalateRequest,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    session_id: Annotated[str, Query(description="Chat session UUID")],
    db: Session = Depends(get_db),
) -> ManualEscalateResponse:
    """Manual escalation for embedded widget (bot public_id + session)."""
    try:
        _bot, tenant = get_bot_and_tenant_for_widget_chat(db, bot_id)
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        if e.reason == WidgetChatTenantGateError.INACTIVE:
            raise HTTPException(status_code=403, detail="Tenant is not active") from e
        raise HTTPException(
            status_code=400,
            detail="Bot configuration is incomplete.",
        ) from e
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid session_id") from None
    trig = (
        EscalationTrigger.user_request
        if body.trigger == "user_request"
        else EscalationTrigger.answer_rejected
    )
    try:
        msg, tnum = perform_manual_escalation(
            db,
            tenant,
            sid,
            api_key=tenant.openai_api_key,
            user_note=body.user_note,
            trigger=trig,
            bot_public_id=bot_id,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    except APIError:
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    return ManualEscalateResponse(message=msg, ticket_number=tnum)
