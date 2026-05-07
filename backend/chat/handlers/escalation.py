"""Escalation state machine — handles the FI-ESC pre-RAG paths.

Encapsulates four escalation states that previously lived inline in
``service.process_chat_message``:

  * ``chat.ended_at is not None``        → chat already closed
  * ``chat.escalation_awaiting_ticket_id`` → awaiting contact email
  * ``chat.escalation_followup_pending``    → follow-up yes/no
  * explicit human request (T-3 trigger) before RAG runs

Behaviour is byte-equivalent to the legacy inline branches; this module is
purely a structural extraction. Persistence helpers, OpenAI escalation calls,
ticket creation and event emission still live in ``backend.chat.service`` and
``backend.escalation.*`` and are looked up lazily to avoid a circular import
with ``service.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.escalation.service import (
    _clear_escalation_clarify_flag,
    _escalation_clarify_already_asked,
    _set_escalation_clarify_flag,
    apply_collected_contact_email,
    detect_human_request,
    get_latest_escalation_ticket_for_chat,
    parse_contact_email,
)
from backend.models import EscalationPhase, EscalationTicket, EscalationTrigger


def _svc_lookup() -> Any:
    """Resolve callables via the service module so test monkeypatches against
    ``backend.chat.service.X`` continue to affect these call sites after the
    move. Inline import avoids a circular dependency at module load.
    """
    from backend.chat import service as _svc
    return _svc

logger = logging.getLogger(__name__)


class EscalationStateMachine(PipelineHandler):
    """Pre-RAG escalation FSM.

    ``can_handle`` is True when the chat is already in any escalation state
    (closed / awaiting email / pending follow-up) or when the user explicitly
    asks for a human in this turn. ``handle`` dispatches to the right internal
    method by checking flags in the same priority order the legacy inline code
    used: closed > awaiting-email > follow-up > explicit-request.
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        chat = ctx.chat
        if chat.ended_at is not None:
            return True
        if chat.escalation_awaiting_ticket_id:
            return True
        if chat.escalation_followup_pending:
            return True
        # Explicit human request runs through the FSM only when not already in
        # a deterministic escalation state above. Detection re-runs here — the
        # call is cheap (regex / phrase match) and keeps the handler self-
        # contained for tests.
        return bool(
            detect_human_request(
                ctx.redacted_question, ctx.api_key, ctx.tenant_id
            )
        )

    async def handle(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        from backend.core.db import run_sync

        return await run_sync(ctx.async_db, lambda sync_db: self._handle_sync(ctx, sync_db))

    def _handle_sync(self, ctx: HandlerContext, sync_db: Session) -> ChatTurnOutcome | None:
        ctx.db = sync_db
        chat = ctx.chat
        if chat.ended_at is not None:
            return self._handle_chat_closed(ctx)
        if chat.escalation_awaiting_ticket_id:
            outcome = self._handle_awaiting_email(ctx)
            if outcome is not None:
                return outcome
            # Ticket disappeared — flag was cleared. Drop into the gates below
            # in case another escalation state still applies; otherwise return
            # None so the router falls through to RagHandler.
        if chat.escalation_followup_pending:
            return self._handle_followup_yes_no(ctx)
        # Explicit human request (T-3) — only fires when the user actually
        # asked for a human. Without this gate, a stale-pointer recovery
        # (vanished awaiting-ticket cleared above) would mint a fresh
        # escalation ticket on any ordinary reply, which the legacy inline
        # flow did not do. Returns None on failure so the router retries
        # with RagHandler.
        if ctx.explicit_human_request:
            return self._handle_explicit_request(ctx)
        return None

    # ------------------------------------------------------------------
    # State handlers — order matches the legacy inline branches in
    # ``process_chat_message`` so the byte-level behaviour is preserved.
    # ------------------------------------------------------------------

    def _handle_chat_closed(self, ctx: HandlerContext) -> ChatTurnOutcome:
        _svc = _svc_lookup()
        msgs = _svc.build_chat_messages_for_openai(ctx.chat, ctx.redacted_question)
        if ctx.trace is not None:
            ctx.trace.span(
                name="chat-state-check",
                input={"state": "closed"},
            ).end(output={"chat_ended": True})
        out = _svc.complete_escalation_openai_turn(
            phase=EscalationPhase.chat_already_closed,
            chat_messages=msgs,
            fact_json={},
            latest_user_text=ctx.redacted_question,
            api_key=ctx.api_key,
            response_language=ctx.language_context.response_language,
        )
        return _svc._escalation_turn_response(
            db=ctx.db,
            chat=ctx.chat,
            tenant_id=ctx.tenant_id,
            language_context=ctx.language_context,
            question=ctx.question,
            out=out,
            optional_entity_types=ctx.optional_entity_types,
            trace=ctx.trace,
            trace_source="chat_closed",
            chat_ended=True,
            escalated=False,
        )

    def _handle_awaiting_email(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        """Returns None if the awaited ticket vanished — caller should fall through."""
        _svc = _svc_lookup()
        chat = ctx.chat
        awaiting_email_span = (
            ctx.trace.span(
                name="escalation-awaiting-email",
                input={"ticket_id": str(chat.escalation_awaiting_ticket_id)},
            )
            if ctx.trace is not None
            else None
        )
        ticket = ctx.db.get(EscalationTicket, chat.escalation_awaiting_ticket_id)
        if not ticket:
            chat.escalation_awaiting_ticket_id = None
            ctx.db.add(chat)
            ctx.db.commit()
            if awaiting_email_span is not None:
                awaiting_email_span.end(output={"ticket_found": False})
            return None

        # Parse contact email from original user text, not redacted text.
        # Redaction replaces addresses with placeholders and would break capture.
        email = parse_contact_email(ctx.question)
        try:
            if email:
                # apply_collected_contact_email flushes (not commits) so all
                # mutations — email, chat flags, and the message turn — commit
                # atomically in _escalation_turn_response below.
                apply_collected_contact_email(ticket.id, chat.id, email, ctx.db)
                ctx.db.refresh(ticket)
                ctx.db.refresh(chat)
                ctx.db.expire(chat, ["messages"])
                msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
                out = _svc.complete_escalation_openai_turn(
                    phase=EscalationPhase.handoff_email_known,
                    chat_messages=msgs,
                    fact_json=_svc.fact_from_ticket(ticket, chat=chat),
                    latest_user_text=ctx.redacted_question,
                    api_key=ctx.api_key,
                    response_language=ctx.language_context.response_language,
                )
                if awaiting_email_span is not None:
                    awaiting_email_span.end(
                        output={"ticket_found": True, "email_captured": True}
                    )
                return _svc._escalation_turn_response(
                    db=ctx.db,
                    chat=chat,
                    tenant_id=ctx.tenant_id,
                    language_context=ctx.language_context,
                    question=ctx.question,
                    out=out,
                    optional_entity_types=ctx.optional_entity_types,
                    trace=ctx.trace,
                    trace_source="escalation_email_capture",
                    chat_ended=False,
                    escalated=True,
                    ticket_number=ticket.ticket_number,
                )
            msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
            out = _svc.complete_escalation_openai_turn(
                phase=EscalationPhase.email_parse_failed,
                chat_messages=msgs,
                fact_json=_svc.fact_from_ticket(ticket, chat=chat),
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
            if awaiting_email_span is not None:
                awaiting_email_span.end(
                    output={"ticket_found": True, "email_captured": False}
                )
            return _svc._escalation_turn_response(
                db=ctx.db,
                chat=chat,
                tenant_id=ctx.tenant_id,
                language_context=ctx.language_context,
                question=ctx.question,
                out=out,
                optional_entity_types=ctx.optional_entity_types,
                trace=ctx.trace,
                trace_source="escalation_email_retry",
                chat_ended=False,
                escalated=True,
            )
        except Exception as exc:
            if awaiting_email_span is not None:
                awaiting_email_span.end(
                    output={"ticket_found": True, "error": True},
                    level="ERROR",
                    status_message=str(exc),
                )
            raise

    def _handle_followup_yes_no(self, ctx: HandlerContext) -> ChatTurnOutcome:
        _svc = _svc_lookup()
        chat = ctx.chat
        followup_span = (
            ctx.trace.span(name="escalation-followup", input={"pending": True})
            if ctx.trace is not None
            else None
        )
        ticket = get_latest_escalation_ticket_for_chat(chat.id, ctx.db)
        msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
        try:
            out = _svc.complete_escalation_openai_turn(
                phase=EscalationPhase.followup_awaiting_yes_no,
                chat_messages=msgs,
                fact_json={
                    **_svc.fact_from_ticket(ticket, chat=chat),
                    "clarify_round": 1 if _escalation_clarify_already_asked(chat) else 0,
                },
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
            decision = out.followup_decision or "unclear"
            if decision == "unclear" and _escalation_clarify_already_asked(chat):
                decision = "yes"
            if decision == "yes":
                chat.escalation_followup_pending = False
                _clear_escalation_clarify_flag(chat)
                ctx.db.add(chat)
                if followup_span is not None:
                    followup_span.end(output={"decision": decision, "chat_ended": False})
                return _svc._escalation_turn_response(
                    db=ctx.db,
                    chat=chat,
                    tenant_id=ctx.tenant_id,
                    language_context=ctx.language_context,
                    question=ctx.question,
                    out=out,
                    optional_entity_types=ctx.optional_entity_types,
                    trace=ctx.trace,
                    trace_source="escalation_followup",
                    chat_ended=False,
                    escalated=True,
                )
            if decision == "no":
                chat.escalation_followup_pending = False
                _clear_escalation_clarify_flag(chat)
                chat.ended_at = datetime.now(UTC)
                ctx.db.add(chat)
                if followup_span is not None:
                    followup_span.end(output={"decision": decision, "chat_ended": True})
                outcome = _svc._escalation_turn_response(
                    db=ctx.db,
                    chat=chat,
                    tenant_id=ctx.tenant_id,
                    language_context=ctx.language_context,
                    question=ctx.question,
                    out=out,
                    optional_entity_types=ctx.optional_entity_types,
                    trace=ctx.trace,
                    trace_source="escalation_followup",
                    chat_ended=True,
                    escalated=True,
                )
                _svc._emit_chat_session_ended_event(
                    tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
                    bot_public_id=ctx.bot_public_id,
                    chat_id=str(chat.id),
                    outcome="resolved",
                )
                return outcome
            _set_escalation_clarify_flag(chat)
            ctx.db.add(chat)
            if followup_span is not None:
                followup_span.end(output={"decision": decision, "chat_ended": False})
            return _svc._escalation_turn_response(
                db=ctx.db,
                chat=chat,
                tenant_id=ctx.tenant_id,
                language_context=ctx.language_context,
                question=ctx.question,
                out=out,
                optional_entity_types=ctx.optional_entity_types,
                trace=ctx.trace,
                trace_source="escalation_followup",
                chat_ended=False,
                escalated=True,
            )
        except Exception as exc:
            if followup_span is not None:
                followup_span.end(
                    output={"error": True},
                    level="ERROR",
                    status_message=str(exc),
                )
            raise

    def _handle_explicit_request(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        """T-3: explicit human request lodged before RAG runs."""
        _svc = _svc_lookup()
        chat = ctx.chat
        msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
        if ctx.trace is not None:
            human_request_span = ctx.trace.span(
                name="human-request-detection",
                input={"question": ctx.redacted_question},
            )
            human_request_span.end(output={"matched": True})
        try:
            ticket = _svc.create_escalation_ticket(
                ctx.tenant_id,
                ctx.question,
                EscalationTrigger.user_request,
                ctx.db,
                chat_id=chat.id,
                session_id=ctx.session_id,
                user_context=ctx.effective_user_ctx,
                optional_entity_types=ctx.optional_entity_types,
            )
            phase = (
                EscalationPhase.handoff_ask_email
                if not ticket.user_email
                else EscalationPhase.handoff_email_known
            )
            out = _svc.complete_escalation_openai_turn(
                phase=phase,
                chat_messages=msgs,
                fact_json=_svc.fact_from_ticket(ticket, chat=chat),
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
            if not ticket.user_email:
                chat.escalation_awaiting_ticket_id = ticket.id
            else:
                chat.escalation_followup_pending = True
            _svc._set_last_response_language(
                db=ctx.db,
                chat=chat,
                tenant_id=ctx.tenant_id,
                response_language=ctx.language_context.response_language,
                resolution_reason=ctx.language_context.response_language_resolution_reason,
                language_context=ctx.language_context,
            )
            ctx.db.add(chat)
            ctx.db.commit()
            user_message, assistant_message = _svc._persist_turn(
                ctx.db,
                chat,
                ctx.tenant_id,
                ctx.question,
                out.message_to_user,
                [],
                out.tokens_used,
                optional_entity_types=ctx.optional_entity_types,
                trace=ctx.trace,
            )
            _svc._try_ingest_gap_signal(
                chat=chat,
                tenant_id=ctx.tenant_id,
                session_id=ctx.session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                question_text=ctx.redacted_question,
                answer_confidence=None,
                was_rejected=False,
                had_fallback=False,
                was_escalated=True,
                language=ctx.language_context.response_language,
            )
            if ctx.trace is not None:
                ctx.trace.update(
                    output={"answer": out.message_to_user, "source": "explicit_handoff"},
                    metadata={
                        "chat_ended": False,
                        "escalated": True,
                        "response_language": ctx.language_context.response_language,
                        "escalation_language": ctx.language_context.escalation_language,
                    },
                )
            _svc._emit_chat_escalated_event(
                tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
                bot_public_id=ctx.bot_public_id,
                chat_id=str(chat.id),
                escalation_reason="explicit_human_request",
                escalation_trigger=EscalationTrigger.user_request.value,
                plan_tier=(ctx.effective_user_ctx or {}).get("plan_tier"),
                priority=ticket.priority if ticket is not None else None,
            )
            _svc._emit_chat_session_ended_event(
                tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
                bot_public_id=ctx.bot_public_id,
                chat_id=str(chat.id),
                outcome="escalated",
            )
            return ChatTurnOutcome(
                text=out.message_to_user,
                document_ids=[],
                tokens_used=out.tokens_used,
                chat_ended=False,
                ticket_number=ticket.ticket_number,
            )
        except Exception as e:
            # Legacy behaviour: log and fall back to the RAG handler so the
            # user still gets a response. Returning None signals the router to
            # try the next handler (RagHandler).
            logger.warning("Escalation T-3 failed, falling back to RAG: %s", e)
            return None
