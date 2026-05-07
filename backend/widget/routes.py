"""Widget API routes for embedded chat (public, bot-id based)."""

import asyncio
import json
import logging
import time
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openai import APIError
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome
from backend.chat.handlers.rag import _CitationStreamFilter
from backend.chat.language import localize_text_to_language_result
from backend.chat.llm_unavailable import classify_llm_failure
from backend.chat.llm_unavailable_copy import fallback_text
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
)
from backend.observability.metrics import capture_event
from backend.tenants.llm_alerts import (
    clear_llm_alert,
    record_llm_failure,
)
from backend.tenants.llm_alerts import (
    is_actionable as is_actionable_llm_failure,
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
    sanitize_user_hints,
    widget_session_error_detail,
)

logger = logging.getLogger(__name__)

widget_router = APIRouter(prefix="/widget", tags=["widget"])
_WIDGET_MESSAGE_MAX_CHARS = settings.widget_message_max_chars


class WidgetSessionInitRequest(BaseModel):
    bot_id: str = Field(..., min_length=1)
    user_hints: dict[str, Any] | None = None
    locale: str | None = Field(default=None, max_length=64)


class WidgetSessionInitResponse(BaseModel):
    session_id: uuid.UUID
    mode: Literal["hints", "anonymous"]


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


@widget_router.post("/session/init", response_model=WidgetSessionInitResponse)
@limiter.limit("10/minute", key_func=widget_init_rate_limit_key)
def widget_session_init(
    request: Request,
    body: Annotated[WidgetSessionInitRequest, Body()],
    db: Session = Depends(get_db),
) -> WidgetSessionInitResponse:
    """
    Start a widget session. Optional `user_hints` attaches untrusted
    personalization fields (name/email/locale/...) supplied by the tenant
    frontend; sessions still work without them.
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
    mode: Literal["hints", "anonymous"] = "anonymous"
    locale = sanitize_locale(body.locale)
    user_context: dict | None = None

    if body.user_hints:
        hints = sanitize_user_hints(body.user_hints)
        if hints:
            # Synthesize a stable user_id when hints carry only an email so
            # ContactSession keying works (its contact_id == user_context.user_id).
            if "user_id" not in hints and "email" in hints:
                hints["user_id"] = f"hint:{hints['email']}"
            user_context = apply_identity_context_patch(
                {"user_id": hints["user_id"]} if "user_id" in hints else {},
                hints,
                browser_locale=locale,
            )
            mode = "hints"
            logger.info(
                "widget_session_init_hints",
                extra={"hint_field_count": len(hints)},
            )

    if user_context is None and locale is not None:
        user_context = {"browser_locale": locale}

    # Always persist the session row so the returned session_id can be used
    # in the next /widget/chat call without hitting session_not_found.
    # Stamp bot_id up front to skip the lazy backfill in widget_chat.
    chat = Chat(
        tenant_id=tenant.id,
        bot_id=_bot.id,
        session_id=session_id,
        user_context=user_context,
    )
    db.add(chat)
    db.flush()
    if mode == "hints" and user_context and user_context.get("user_id"):
        start_user_session(
            db,
            tenant_id=tenant.id,
            user_context=user_context,
            started_at=chat.created_at,
        )
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

    return _widget_chat_stream(
        sid,
        process_kwargs,
        tenant_public_id=getattr(tenant, "public_id", None),
        bot_public_id=getattr(_bot, "public_id", None),
        is_greeting=resolved_message == "",
    )


_STREAM_SENTINEL = object()


def _apply_llm_alert_side_effect(
    tenant_id: uuid.UUID,
    failure_type: str | None,
) -> None:
    """Sync write hook called from the async widget pipeline.

    For an actionable failure (quota_exhausted / invalid_api_key) records
    the alert and emails the tenant admin (throttled to 24h). For a
    successful turn (failure_type=None) clears any active alert. Other
    failure types (transient, timeout, rate_limited) are no-ops here —
    they shouldn't surface a tenant-action banner.
    """
    try:
        with core_db.SessionLocal() as session:
            tenant = session.get(Tenant, tenant_id)
            if tenant is None:
                return
            if failure_type is None:
                clear_llm_alert(session, tenant)
            elif is_actionable_llm_failure(failure_type):
                record_llm_failure(session, tenant, failure_type)
    except Exception:
        logger.warning("widget_llm_alert_side_effect_failed", exc_info=True)


def _emit_first_token_metric(
    *,
    sid: uuid.UUID,
    t_start: float,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    is_greeting: bool,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    ms = round((time.monotonic() - t_start) * 1000)
    try:
        capture_event(
            "chat_first_token_ms",
            distinct_id=str(sid),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "chat_first_token_ms": ms,
                "session_id": str(sid),
                "is_greeting": is_greeting,
            },
            groups={"tenant": tenant_public_id} if tenant_public_id else None,
        )
    except Exception:
        logger.warning("first_token_metric_emit_failed", exc_info=True)


def _widget_chat_stream(
    sid: uuid.UUID,
    process_kwargs: dict,
    *,
    tenant_public_id: str | None = None,
    bot_public_id: str | None = None,
    is_greeting: bool = False,
) -> StreamingResponse:
    async def event_stream():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Any] = asyncio.Queue()
        result_holder: dict[str, Any] = {}
        # Server-side TTFB: time from request entry to the first chunk we
        # actually emit downstream. Client-side posthog.capture is unreliable
        # in the embedded iframe (storage partitioning / extensions silently
        # block /ingest), so we measure here and emit via the backend SDK.
        t_start = time.monotonic()

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
            except APIError as exc:
                # LLM provider unavailable. Convert to a degraded outcome with
                # a typed failure_state instead of a raw error event so the
                # widget can render Try again / Contact support buttons.
                # No support ticket is created here (spec rule: LLM failure
                # is a degraded service state, not an escalation event).
                failure_state = classify_llm_failure(exc)
                language = process_kwargs.get("browser_locale")
                text = fallback_text(
                    language=language,
                    retryable=failure_state.retryable,
                )
                result_holder["outcome"] = ChatTurnOutcome(
                    text=text,
                    document_ids=[],
                    tokens_used=0,
                    chat_ended=False,
                    failure_state=failure_state,
                )
                logger.info(
                    "widget_chat_llm_unavailable",
                    extra={
                        "failure_type": failure_state.type.value,
                        "retryable": failure_state.retryable,
                        "session_id": str(sid),
                    },
                )
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
                    if not streamed_any:
                        _emit_first_token_metric(
                            sid=sid,
                            t_start=t_start,
                            tenant_public_id=tenant_public_id,
                            bot_public_id=bot_public_id,
                            is_greeting=is_greeting,
                        )
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
        # Tenant-level alert side-effect runs in a fresh sync session — the
        # async pipeline session is already gone by here, and we want the
        # write to commit independently of any later SSE failure. Only
        # actionable failure types raise (or clear) the alert; other
        # outcomes are no-ops.
        if outcome is not None:
            tenant_id_value = process_kwargs.get("tenant_id")
            if tenant_id_value is not None:
                await asyncio.to_thread(
                    _apply_llm_alert_side_effect,
                    tenant_id_value,
                    outcome.failure_state.type.value if outcome.failure_state else None,
                )
        final_text = outcome.text if outcome is not None else ""
        is_llm_unavailable = (
            outcome is not None and outcome.failure_state is not None
        )
        # Suppress streamed-chunk replay for the degraded path: no chunks
        # were produced (LLM failed before any token), and emitting the
        # fallback as a "chunk" before the "done" event would leak it into
        # any naive client buffer.
        if not streamed_any and final_text and not is_llm_unavailable:
            _emit_first_token_metric(
                sid=sid,
                t_start=t_start,
                tenant_public_id=tenant_public_id,
                bot_public_id=bot_public_id,
                is_greeting=is_greeting,
            )
            yield f"data: {json.dumps({'type': 'chunk', 'text': final_text})}\n\n"
        turn_response = WidgetChatTurnResponse(
            text=final_text,
            session_id=sid,
            chat_ended=bool(outcome.chat_ended) if outcome is not None else False,
            ticket_number=outcome.ticket_number if outcome is not None else None,
            outcome="llm_unavailable" if is_llm_unavailable else None,
            failure_state=outcome.failure_state if is_llm_unavailable else None,
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
    trig = {
        "user_request": EscalationTrigger.user_request,
        "answer_rejected": EscalationTrigger.answer_rejected,
        "llm_unavailable": EscalationTrigger.llm_unavailable,
    }[body.trigger]
    try:
        msg, tnum = perform_manual_escalation(
            db,
            tenant,
            sid,
            api_key=tenant.openai_api_key,
            user_note=body.user_note,
            trigger=trig,
            bot_public_id=bot_id,
            failure_type=body.failure_type,
            original_user_message=body.original_user_message,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    except APIError:
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    return ManualEscalateResponse(message=msg, ticket_number=tnum)
