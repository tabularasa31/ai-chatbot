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
from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat.handlers.base import ChatTurnOutcome
from backend.chat.handlers.rag import _CitationStreamFilter
from backend.chat.language import async_localize_text_to_language_result
from backend.chat.llm_unavailable import classify_llm_failure
from backend.chat.llm_unavailable_copy import fallback_text
from backend.chat.rotation import should_rotate
from backend.chat.schemas import WidgetChatTurnResponse
from backend.chat.service import async_process_chat_message
from backend.contact_sessions.service import (
    start_user_session,
    sync_user_session_identity,
)
from backend.core import db as core_db
from backend.core.config import settings
from backend.core.db import get_async_db, run_sync
from backend.core.limiter import (
    limiter,
    widget_bot_rate_limit_key,
    widget_init_rate_limit_key,
    widget_poll_rate_limit_key,
    widget_public_rate_limit_key,
)
from backend.escalation.schemas import ManualEscalateRequest, ManualEscalateResponse
from backend.escalation.service import ACTIVE_TICKET_STATUSES, perform_manual_escalation
from backend.models import (
    Chat,
    Document,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    OperatorState,
)
from backend.observability.metrics import capture_event
from backend.tenants.llm_alerts import (
    apply_clear_alert,
    apply_llm_failure,
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
    resumed: bool = False


class WidgetChatRequest(BaseModel):
    message: str | None = None
    locale: str | None = Field(default=None, max_length=64)


class WidgetLinkSafetyLabels(BaseModel):
    title: str
    body: str
    continue_label: str
    cancel_label: str


#: What the widget writes above a human's reply. Deliberately a fixed English
#: word and not localized: the owner's call, so it stays out of the
#: "every visitor-facing string is localized" rule.
OPERATOR_LABEL = "Operator"


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


async def _link_safety_labels(
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

    async def localize(canonical_text: str) -> str:
        result = await async_localize_text_to_language_result(
            canonical_text=canonical_text,
            target_language=target_language,
            api_key=encrypted_api_key,
            fallback_locale=target_language,
            operation="widget_link_safety_localize",
            tenant_id=tenant_id,
            bot_id=bot_id,
        )
        return result.text

    title, body, continue_label, cancel_label = await asyncio.gather(
        localize(labels.title),
        localize(labels.body),
        localize(labels.continue_label),
        localize(labels.cancel_label),
    )
    return WidgetLinkSafetyLabels(
        title=title,
        body=body,
        continue_label=continue_label,
        cancel_label=cancel_label,
    )


@widget_router.get("/config", response_model=WidgetConfigResponse)
@limiter.limit("30/minute", key_func=widget_public_rate_limit_key)
async def widget_config(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    locale: Annotated[str | None, Query(description="Browser locale hint (e.g. ru-RU)")] = None,
    db: AsyncSession = Depends(get_async_db),
) -> WidgetConfigResponse:
    try:
        bot, tenant = await run_sync(
            db, lambda s: get_bot_and_tenant_for_widget_chat(s, bot_id)
        )
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
        await _link_safety_labels(
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
async def widget_session_init(
    request: Request,
    body: Annotated[WidgetSessionInitRequest, Body()],
    db: AsyncSession = Depends(get_async_db),
) -> WidgetSessionInitResponse:
    """
    Start a widget session. Optional `user_hints` attaches untrusted
    personalization fields (name/email/locale/...) supplied by the tenant
    frontend; sessions still work without them.
    """
    try:
        _bot, tenant = await run_sync(
            db, lambda s: get_bot_and_tenant_for_widget_session(s, body.bot_id)
        )
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
    # Only an explicit user_id is eligible for cross-device resume. An email is
    # too guessable to safely reattach to another visitor's live conversation
    # over a public endpoint, so the synthesized hint:<email> id never resumes.
    resume_eligible = False

    if body.user_hints:
        hints = sanitize_user_hints(body.user_hints)
        if hints:
            resume_eligible = "user_id" in hints
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

    # Identified users resume their most recent still-open session so history
    # survives cleared localStorage and follows them across devices.
    if resume_eligible and user_context and user_context.get("user_id"):

        def _resume_existing(s):
            existing = (
                s.query(Chat)
                .filter(
                    Chat.tenant_id == tenant.id,
                    Chat.bot_id == _bot.id,
                    Chat.ended_at.is_(None),
                    Chat.user_context["user_id"].as_string()
                    == user_context["user_id"],
                )
                .order_by(Chat.created_at.desc())
                .first()
            )
            if existing is None:
                return None
            existing.user_context = apply_identity_context_patch(
                existing.user_context,
                user_context,
                browser_locale=locale,
            )
            sync_user_session_identity(
                s,
                tenant_id=tenant.id,
                user_context=existing.user_context,
            )
            s.commit()
            return existing.session_id

        resumed_session_id = await run_sync(db, _resume_existing)
        if resumed_session_id is not None:
            logger.info("widget_session_init_resumed")
            return WidgetSessionInitResponse(
                session_id=resumed_session_id, mode=mode, resumed=True
            )

    # Always persist the session row so the returned session_id can be used
    # in the next /widget/chat call without hitting session_not_found.
    # Stamp bot_id up front to skip the lazy backfill in widget_chat.
    def _create_session(s):
        chat = Chat(
            tenant_id=tenant.id,
            bot_id=_bot.id,
            session_id=session_id,
            user_context=user_context,
        )
        s.add(chat)
        s.flush()
        if mode == "hints" and user_context and user_context.get("user_id"):
            start_user_session(
                s,
                tenant_id=tenant.id,
                user_context=user_context,
                started_at=chat.created_at,
            )
        s.commit()

    await run_sync(db, _create_session)

    return WidgetSessionInitResponse(session_id=session_id, mode=mode, resumed=False)


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
async def widget_chat(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    body: Annotated[WidgetChatRequest | None, Body()] = None,
    session_id: Annotated[str | None, Query(description="Optional session ID")] = None,
    locale: Annotated[
        str | None, Query(description="Browser locale hint (e.g. ru-RU)")
    ] = None,
    db: AsyncSession = Depends(get_async_db),
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
        _bot, tenant = await run_sync(
            db, lambda s: get_bot_and_tenant_for_widget_chat(s, bot_id)
        )
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        if e.reason == WidgetChatTenantGateError.INACTIVE:
            raise HTTPException(status_code=403, detail="Tenant is not active") from e
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        ) from e

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

        def _lookup_existing_chat(s):
            existing_chat = (
                s.query(Chat)
                .filter(
                    Chat.tenant_id == tenant.id,
                    Chat.session_id == sid,
                    or_(Chat.bot_id == _bot.id, Chat.bot_id.is_(None)),
                )
                .order_by(Chat.created_at.desc())
                .first()
            )
            if existing_chat is None:
                return None
            rotation_pending = should_rotate(existing_chat)
            if existing_chat.bot_id is None and not rotation_pending:
                # Skip the backfill when rotation is pending: the commit would
                # refresh updated_at (onupdate) and make the pipeline see the
                # stale chat as fresh; the new Chat gets its bot_id on creation.
                existing_chat.bot_id = _bot.id
                s.add(existing_chat)
                s.commit()
            return rotation_pending, existing_chat.ended_at is not None

        chat_state = await run_sync(db, _lookup_existing_chat)
        if chat_state is None:
            raise HTTPException(
                status_code=409,
                detail=widget_session_error_detail(
                    SESSION_NOT_FOUND_CODE,
                    "Session not found",
                ),
            )
        rotation_pending, chat_ended = chat_state
        if chat_ended and not rotation_pending:
            # Within the idle window a closed chat still answers with the
            # "already closed" acknowledgement; past it, the turn falls
            # through and rotation opens a fresh conversation instead.
            raise HTTPException(
                status_code=409,
                detail=widget_session_error_detail(
                    SESSION_CLOSED_CODE,
                    "Session is closed",
                ),
            )
    else:
        sid = uuid.uuid4()
        rotation_pending = False

    if not resolved_message:
        if session_id and not rotation_pending:
            logger.info(
                "widget_message_rejected",
                extra={"reason": "empty", "length": 0},
            )
            raise HTTPException(
                status_code=422,
                detail={"code": "message_required", "message": "message is required"},
            )
        # Bootstrap turn: a brand-new session, or a rotated conversation
        # re-greeting a returning visitor (the pipeline opens the new Chat).
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

    # All request-scoped DB work is done; the pipeline below runs on its own
    # AsyncSessionLocal. Release this session's pooled connection now —
    # FastAPI closes yield-dependencies only after the response finishes,
    # which for SSE would pin the connection for the whole stream.
    await db.close()

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
    """Sync write hook called from the async widget pipeline via to_thread.

    For an actionable failure (quota_exhausted / invalid_api_key) records
    the alert and emails the tenant owner (throttled to 24h). For a
    cleared signal (failure_type=None) clears any active alert. Other
    failure types are no-ops here.

    The caller is responsible for deciding *when* to pass ``None`` —
    successful greeting / small-talk turns that didn't actually call the
    LLM must not be treated as evidence the LLM is healthy.
    """
    try:
        if failure_type is None:
            apply_clear_alert(tenant_id)
        else:
            apply_llm_failure(tenant_id, failure_type)
    except Exception:
        logger.warning("widget_llm_alert_side_effect_failed", exc_info=True)


def _emit_first_token_metric(
    *,
    sid: uuid.UUID,
    ttft_ms: int,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    is_greeting: bool,
    chat_id: str | None = None,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        capture_event(
            "chat_first_token_ms",
            distinct_id=str(sid),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "ttft_ms": ttft_ms,
                "chat_first_token_ms": ttft_ms,  # backward-compat alias
                "session_id": str(sid),
                "chat_id": chat_id,
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
                        # Record TTFT now (accurate wall-clock), emit after
                        # await task so chat_id from outcome is available.
                        result_holder["ttft_ms"] = round(
                            (time.monotonic() - t_start) * 1000
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
        # Tenant-level alert side-effect runs in a fresh sync session via
        # to_thread (off the event loop) so the blocking httpx send doesn't
        # freeze the SSE stream. We only signal "LLM healthy" (failure_type
        # = None ⇒ clear) when the turn actually exercised the provider:
        # tokens_used > 0 is the proxy. Greeting / small-talk handlers can
        # return a successful outcome without ever calling OpenAI; treating
        # those as evidence of recovery would clear the banner while the
        # underlying key is still broken.
        if outcome is not None:
            tenant_id_value = process_kwargs.get("tenant_id")
            if tenant_id_value is not None:
                if outcome.failure_state is not None:
                    await asyncio.to_thread(
                        _apply_llm_alert_side_effect,
                        tenant_id_value,
                        outcome.failure_state.type.value,
                    )
                elif outcome.tokens_used > 0:
                    await asyncio.to_thread(
                        _apply_llm_alert_side_effect,
                        tenant_id_value,
                        None,
                    )
        final_text = outcome.text if outcome is not None else ""
        is_llm_unavailable = (
            outcome is not None and outcome.failure_state is not None
        )
        # A human operator holds this chat: the visitor's message was persisted
        # and nothing was generated. Same suppression as the degraded path
        # below, for the same reason — there is no bot reply to replay.
        delivered_to_operator = (
            outcome is not None and outcome.delivered_to_operator
        )
        # Suppress streamed-chunk replay for the degraded path: no chunks
        # were produced (LLM failed before any token), and emitting the
        # fallback as a "chunk" before the "done" event would leak it into
        # any naive client buffer.
        if not streamed_any and final_text and not is_llm_unavailable and not delivered_to_operator:
            # Non-streaming fallback: TTFT = full pipeline latency.
            result_holder["ttft_ms"] = round((time.monotonic() - t_start) * 1000)
            yield f"data: {json.dumps({'type': 'chunk', 'text': final_text})}\n\n"
        # Emit TTFT metric once, after pipeline completes, so chat_id from the
        # outcome is available for joining with chat.turn / chat_completed.
        ttft_ms = result_holder.get("ttft_ms")
        if ttft_ms is not None and not is_llm_unavailable and not delivered_to_operator:
            _emit_first_token_metric(
                sid=sid,
                ttft_ms=ttft_ms,
                tenant_public_id=tenant_public_id,
                bot_public_id=bot_public_id,
                is_greeting=is_greeting,
                chat_id=outcome.chat_id if outcome is not None else None,
            )
        turn_response = WidgetChatTurnResponse(
            text=final_text,
            session_id=sid,
            chat_ended=bool(outcome.chat_ended) if outcome is not None else False,
            ticket_number=outcome.ticket_number if outcome is not None else None,
            outcome="llm_unavailable" if is_llm_unavailable else None,
            failure_state=outcome.failure_state if is_llm_unavailable else None,
            delivered_to_operator=delivered_to_operator,
            escalation_offered=(
                bool(outcome.escalation_offered) if outcome is not None else False
            ),
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


#: Roles the visitor is allowed to see. ``operator`` belongs here as much as
#: ``assistant`` does: a human answering through the console or the inbound
#: e-mail lane writes into the same thread, and filtering it out is what made
#: the whole handoff invisible in the widget. An allowlist rather than a
#: dropped filter so a future internal role does not leak by default.
_VISITOR_VISIBLE_ROLES = (MessageRole.user, MessageRole.assistant, MessageRole.operator)

#: Ceiling on one cursor poll. A conversation that outran the client's cursor
#: by more than this is pathological; the tail is what matters.
_POLL_MESSAGE_LIMIT = 100


class WidgetHistoryMessage(BaseModel):
    #: Server-side message id — the widget's polling cursor starts here.
    id: uuid.UUID
    role: str
    content: str


def _handoff_state(s, chat: Chat) -> str:
    """``live`` / ``waiting`` / ``bot`` for the conversation, derived not stored.

    There are deliberately only two stored states (``chats.operator_state``);
    "waiting for a human" is read off the data — an active ticket nobody has
    answered — rather than kept as a third value that would have to be held in
    step with the escalation automaton. The widget uses this to decide how
    often to poll: not at all under ``bot``, slowly while waiting, briskly
    while a human is actually typing.
    """
    if chat.operator_state is OperatorState.live:
        return "live"
    has_open_ticket = (
        s.query(EscalationTicket.id)
        .filter(
            EscalationTicket.chat_id == chat.id,
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
        )
        .first()
        is not None
    )
    return "waiting" if has_open_ticket else "bot"


class WidgetHistoryResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[WidgetHistoryMessage]
    chat_ended: bool
    ticket_number: str | None = None
    #: ``bot`` | ``waiting`` | ``live`` — see :func:`_handoff_state`.
    handoff_state: str = "bot"
    #: Byline the widget writes above an operator-authored message.
    operator_label: str = OPERATOR_LABEL
    # Message indices where a newer conversation begins — the widget renders
    # a "new conversation" separator before each of them.
    boundary_indices: list[int] = []
    # True when the session's latest conversation is idle past the rotation
    # threshold: the widget shows a separator and fetches a fresh greeting
    # (the next POST /widget/chat rotates server-side).
    conversation_rotated: bool = False


@widget_router.get("/history", response_model=WidgetHistoryResponse)
@limiter.limit("30/minute", key_func=widget_public_rate_limit_key)
async def widget_history(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    session_id: Annotated[str, Query(description="Chat session UUID")],
    db: AsyncSession = Depends(get_async_db),
) -> WidgetHistoryResponse:
    """Return message history for a widget session (public, no auth)."""
    try:
        _bot, tenant = await run_sync(
            db, lambda s: get_bot_and_tenant_for_widget_chat(s, bot_id)
        )
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        raise HTTPException(status_code=400, detail="Bot not available") from e

    try:
        sid = uuid.UUID(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid session_id") from None

    def _load_history(s) -> WidgetHistoryResponse | None:
        # Latest two conversations: the current one plus the previous one as
        # read-only context after rotation (oldest first after the slice).
        chats = (
            s.query(Chat)
            .filter(
                Chat.tenant_id == tenant.id,
                Chat.session_id == sid,
                or_(Chat.bot_id == _bot.id, Chat.bot_id.is_(None)),
            )
            .order_by(Chat.created_at.desc())
            .limit(2)
            .all()
        )
        if not chats:
            return None
        chats.reverse()
        latest = chats[-1]

        messages = (
            s.query(Message)
            .filter(
                Message.chat_id.in_([c.id for c in chats]),
                Message.role.in_(_VISITOR_VISIBLE_ROLES),
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )

        boundary_indices = [
            idx
            for idx, m in enumerate(messages)
            if idx > 0 and m.chat_id != messages[idx - 1].chat_id
        ]
        # Read-only signal: the actual rotation happens on the next POST.
        # Suppress the auto re-greet when the idle conversation never got a
        # user turn (greeting-only): rotating it would just churn another
        # empty greeting Chat row + trace for a visitor who never engaged.
        # A real message after idle still rotates server-side (widget_chat
        # re-checks should_rotate), so an engaged returning visitor keeps
        # getting a fresh conversation.
        latest_has_user_turn = any(
            m.chat_id == latest.id and m.role == MessageRole.user for m in messages
        )
        conversation_rotated = should_rotate(latest) and latest_has_user_turn

        ticket_number: str | None = None
        if latest.escalation_awaiting_ticket_id is not None:
            ticket = s.get(EscalationTicket, latest.escalation_awaiting_ticket_id)
            if ticket is not None:
                ticket_number = ticket.ticket_number

        # A rotated-away closed chat must not lock the widget input — the
        # visitor is about to start a fresh conversation.
        return WidgetHistoryResponse(
            session_id=sid,
            messages=[
                WidgetHistoryMessage(id=m.id, role=m.role.value, content=m.content)
                for m in messages
            ],
            chat_ended=latest.ended_at is not None and not conversation_rotated,
            ticket_number=ticket_number,
            boundary_indices=boundary_indices,
            conversation_rotated=conversation_rotated,
            handoff_state=_handoff_state(s, latest),
        )

    history = await run_sync(db, _load_history)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return history


class WidgetMessagesResponse(BaseModel):
    session_id: uuid.UUID
    #: Everything written after the cursor, oldest first.
    messages: list[WidgetHistoryMessage]
    #: ``bot`` | ``waiting`` | ``live`` — see :func:`_handoff_state`.
    handoff_state: str = "bot"
    chat_ended: bool = False
    #: Byline for operator-authored messages.
    operator_label: str = OPERATOR_LABEL
    #: The cursor named a message this conversation does not contain (the
    #: conversation rotated, or the widget is holding an id from a session that
    #: has since been replaced). The client re-fetches ``/history`` instead of
    #: trying to splice an unrelated tail onto what it is showing.
    cursor_stale: bool = False


@widget_router.get("/messages", response_model=WidgetMessagesResponse)
# Two limits, and neither replaces the other. The session key shares the budget
# out per conversation, so visitors behind one office NAT stop starving each
# other -- but it is a client-supplied string, so on its own it is no limit at
# all. The address key is the ceiling: loose enough for a busy shared address
# (a live poll is ~24/min per visitor), tight enough to bound one caller
# rotating session ids against an endpoint that does two queries per call.
@limiter.limit("600/minute", key_func=widget_public_rate_limit_key)
@limiter.limit("120/minute", key_func=widget_poll_rate_limit_key)
async def widget_messages(
    request: Request,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    session_id: Annotated[str, Query(description="Chat session UUID")],
    after_message_id: Annotated[
        str | None, Query(description="Return messages written after this one")
    ] = None,
    db: AsyncSession = Depends(get_async_db),
) -> WidgetMessagesResponse:
    """Incremental tail of the current conversation (public, no auth).

    Separate from ``/history`` rather than a mode of it, because they answer
    different questions and pay different prices. ``/history`` bootstraps: two
    conversations, rotation, separators, the ticket number — everything the
    widget needs once, on mount. This is the one the widget calls every few
    seconds while a human is answering, so it reads one conversation and
    returns only what the caller has not seen.

    The cursor is an opaque message id rather than a timestamp or an offset.
    That is what lets this become a long-poll or an SSE stream later without
    the client's logic changing: "everything after X" is the same request
    whether the answer comes back immediately, in thirty seconds, or as a
    stream of pushes. A timestamp cursor would have had to grow tie-breaking
    rules the moment two messages shared a second, and an offset would have
    broken the first time anything was inserted.

    Resolved by position rather than by comparing timestamps: the cursor is
    located in the conversation's own ordering and everything after it is
    returned. Two messages written in the same second cannot make it skip one.
    """
    try:
        _bot, tenant = await run_sync(
            db, lambda s: get_bot_and_tenant_for_widget_chat(s, bot_id)
        )
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Bot not found") from e
        raise HTTPException(status_code=400, detail="Bot not available") from e

    try:
        sid = uuid.UUID(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid session_id") from None

    cursor: uuid.UUID | None = None
    if after_message_id:
        try:
            cursor = uuid.UUID(after_message_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422, detail="Invalid after_message_id"
            ) from None

    def _load_tail(s) -> WidgetMessagesResponse | None:
        chat = (
            s.query(Chat)
            .filter(
                Chat.tenant_id == tenant.id,
                Chat.session_id == sid,
                or_(Chat.bot_id == _bot.id, Chat.bot_id.is_(None)),
            )
            .order_by(Chat.created_at.desc())
            .first()
        )
        if chat is None:
            return None

        rows = (
            s.query(Message)
            .filter(
                Message.chat_id == chat.id,
                Message.role.in_(_VISITOR_VISIBLE_ROLES),
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )

        # Captured before the cursor slice: ``/history`` decides "ended" from
        # the whole conversation, and reading it off a tail would answer
        # differently on every poll.
        has_user_turn = any(m.role == MessageRole.user for m in rows)

        cursor_stale = False
        if cursor is not None:
            index = next(
                (i for i, m in enumerate(rows) if m.id == cursor),
                None,
            )
            if index is None:
                # The cursor belongs to an older conversation (rotation) or to
                # a session this browser no longer holds. Returning this
                # conversation's whole tail would duplicate everything the
                # widget already shows, so say so and let it re-bootstrap.
                cursor_stale = True
                rows = []
            else:
                rows = rows[index + 1 :]

        # The *oldest* unseen messages, not the newest. Truncating from the
        # front would hand back the tail and leave the client's cursor past
        # everything it skipped, losing those messages for good. Taking the
        # front means a backlog is drained over consecutive polls instead.
        page = rows[:_POLL_MESSAGE_LIMIT]

        return WidgetMessagesResponse(
            session_id=sid,
            messages=[
                WidgetHistoryMessage(id=m.id, role=m.role.value, content=m.content)
                for m in page
            ],
            handoff_state=_handoff_state(s, chat),
            # Same expression as ``/history``, deliberately. A closed chat that
            # has gone idle past the rotation threshold is about to be replaced
            # by a fresh one, so neither endpoint calls it ended -- and the two
            # disagreeing would flip the widget between locked and unlocked as
            # the bootstrap and the poll took turns answering.
            chat_ended=(
                chat.ended_at is not None
                and not (should_rotate(chat) and has_user_turn)
            ),
            cursor_stale=cursor_stale,
        )

    tail = await run_sync(db, _load_tail)
    if tail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return tail


@widget_router.post("/escalate", response_model=ManualEscalateResponse)
@limiter.limit("20/minute", key_func=widget_public_rate_limit_key)
async def widget_escalate(
    request: Request,
    body: ManualEscalateRequest,
    bot_id: Annotated[str, Query(description="Bot public ID")],
    session_id: Annotated[str, Query(description="Chat session UUID")],
    db: AsyncSession = Depends(get_async_db),
) -> ManualEscalateResponse:
    """Manual escalation for embedded widget (bot public_id + session)."""
    try:
        _bot, tenant = await run_sync(
            db, lambda s: get_bot_and_tenant_for_widget_chat(s, bot_id)
        )
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
        msg, tnum = await perform_manual_escalation(
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
