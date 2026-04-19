"""Widget API routes for embedded chat (public, bot-id based)."""

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from openai import APIError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.chat.service import process_chat_message
from backend.core.config import settings
from backend.core.db import get_db
from backend.core.limiter import (
    limiter,
    widget_init_rate_limit_key,
    widget_public_rate_limit_key,
    widget_tenant_rate_limit_key,
)
from backend.core.security import validate_kyc_token_detail
from backend.escalation.schemas import ManualEscalateRequest, ManualEscalateResponse
from backend.escalation.service import perform_manual_escalation
from backend.models import Chat, EscalationTrigger, Tenant, UserContext
from backend.tenants.service import get_kyc_decrypted_keys_for_validation
from backend.tenants.widget_chat_gate import (
    WidgetChatTenantGateError,
    get_tenant_eligible_for_widget_chat,
)
from backend.user_sessions.service import start_user_session, touch_user_session
from backend.widget.service import (
    SESSION_CLOSED_CODE,
    SESSION_INVALID_CODE,
    SESSION_NOT_FOUND_CODE,
    apply_identity_context_patch,
    find_resumable_identified_chat,
    sanitize_locale,
    widget_session_error_detail,
)

logger = logging.getLogger(__name__)

widget_router = APIRouter(prefix="/widget", tags=["widget"])
_WIDGET_MESSAGE_MAX_CHARS = settings.widget_message_max_chars


class WidgetSessionInitRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    identity_token: str | None = None
    locale: str | None = Field(default=None, max_length=64)


class WidgetSessionInitResponse(BaseModel):
    session_id: uuid.UUID
    mode: Literal["identified", "anonymous"]


class WidgetChatRequest(BaseModel):
    message: str | None = None
    locale: str | None = Field(default=None, max_length=64)


@widget_router.get("/health")
def widget_health() -> dict[str, str]:
    """Health check for widget endpoints."""
    return {"status": "ok"}


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
            identity_token.strip(), sk, tenant.public_id
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
    tenant = (
        db.query(Tenant)
        .filter(Tenant.api_key == body.api_key.strip())
        .first()
    )
    if not tenant or not tenant.is_active:
        logger.info(
            "widget_session_init_rejected",
            extra={"reason": "not_found" if not tenant else "inactive"},
        )
        raise HTTPException(status_code=404, detail="Invalid API key")

    session_id = uuid.uuid4()
    mode: Literal["identified", "anonymous"] = "anonymous"
    locale = sanitize_locale(body.locale)

    ctx, fail_reason = _resolve_widget_identity(tenant, body.identity_token)
    if ctx is not None:
        resumable_chat = find_resumable_identified_chat(
            db,
            tenant_id=tenant.id,
            user_id=ctx["user_id"],
        )
        if resumable_chat is not None:
            session_id = resumable_chat.session_id
            resumable_chat.user_context = apply_identity_context_patch(
                resumable_chat.user_context,
                ctx,
                browser_locale=locale,
            )
            db.add(resumable_chat)
            touch_user_session(
                db,
                tenant_id=tenant.id,
                user_context=resumable_chat.user_context,
                started_at=resumable_chat.created_at,
            )
            db.commit()
            logger.info("kyc_session_resumed: tenant_id=%s", tenant.id)
        else:
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
            logger.info(
                "kyc_session_resume_skipped: tenant_id=%s reason=no_resumable_session",
                tenant.id,
            )
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


@widget_router.post("/chat")
@limiter.limit(
    settings.effective_widget_chat_per_client_rate,
    key_func=widget_tenant_rate_limit_key,
)
@limiter.limit("30/minute", key_func=widget_public_rate_limit_key)
def widget_chat(
    request: Request,
    tenant_id: Annotated[str, Query(description="Public tenant ID (ch_xyz)")],
    body: Annotated[WidgetChatRequest | None, Body()] = None,
    session_id: Annotated[str | None, Query(description="Optional session ID")] = None,
    locale: Annotated[
        str | None, Query(description="Browser locale hint (e.g. ru-RU)")
    ] = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    PUBLIC endpoint for embedded widget.
    No authentication required (public bot ID = permission).
    """
    resolved_message = body.message if body is not None else None
    if resolved_message is not None:
        resolved_message = resolved_message.strip()
    if not resolved_message:
        logger.info(
            "widget_message_rejected",
            extra={"reason": "empty", "length": 0},
        )
        raise HTTPException(
            status_code=422,
            detail={"code": "message_required", "message": "message is required"},
        )
    if len(resolved_message) > _WIDGET_MESSAGE_MAX_CHARS:
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

    locale_hint = sanitize_locale((body.locale if body is not None else None) or locale)

    try:
        tenant = get_tenant_eligible_for_widget_chat(db, tenant_id)
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Tenant not found") from e
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
            .filter(Chat.tenant_id == tenant.id, Chat.session_id == sid)
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

    try:
        outcome = process_chat_message(
            tenant_id=tenant.id,
            question=resolved_message,
            session_id=sid,
            db=db,
            api_key=tenant.openai_api_key,
            user_context=None,
            browser_locale=locale_hint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        ) from None

    return {
        "text": outcome.text,
        "session_id": str(sid),
        "chat_ended": outcome.chat_ended,
    }


@widget_router.post("/escalate", response_model=ManualEscalateResponse)
@limiter.limit("20/minute", key_func=widget_public_rate_limit_key)
def widget_escalate(
    request: Request,
    body: ManualEscalateRequest,
    tenant_id: Annotated[str, Query(description="Public tenant ID (ch_xyz)")],
    session_id: Annotated[str, Query(description="Chat session UUID")],
    db: Session = Depends(get_db),
) -> ManualEscalateResponse:
    """Manual escalation for embedded widget (public bot ID + session)."""
    try:
        tenant = get_tenant_eligible_for_widget_chat(db, tenant_id)
    except WidgetChatTenantGateError as e:
        if e.reason == WidgetChatTenantGateError.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Tenant not found") from e
        if e.reason == WidgetChatTenantGateError.INACTIVE:
            raise HTTPException(status_code=403, detail="Tenant is not active") from e
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
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
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    except APIError:
        raise HTTPException(status_code=503, detail="OpenAI service unavailable") from None
    return ManualEscalateResponse(message=msg, ticket_number=tnum)
