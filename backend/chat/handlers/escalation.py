"""Escalation state machine — handles the FI-ESC pre-RAG paths.

States, in priority order:

  * ``chat.ended_at is not None``            → chat already closed
  * ``chat.escalation_awaiting_ticket_id``   → awaiting contact email
  * ``chat.escalation_followup_pending``      → follow-up yes/no
  * ``chat.escalation_pre_confirm_pending``  → waiting for the user to
    confirm the human handoff *and* provide a substantive description. The
    ticket is created only on this confirmation turn, using whichever text
    is most descriptive (current message if substantive, else the stored
    original "I need an operator" phrase).
  * explicit human request (T-3 trigger) before RAG runs — initiates the
    pre_confirm phase above instead of minting a ticket immediately.

Persistence helpers, OpenAI escalation calls, ticket creation and event
emission live in ``backend.chat.service`` and ``backend.escalation.*`` and
are looked up lazily to avoid a circular import with ``service.py``.
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
    _pre_confirm_clarify_already_asked,
    _set_escalation_clarify_flag,
    _set_pre_confirm_clarify_flag,
    apply_collected_contact_email,
    clear_pre_confirm_state,
    get_latest_escalation_ticket_for_chat,
    parse_contact_email,
    set_pre_confirm_state,
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
        if getattr(chat, "escalation_pre_confirm_pending", False):
            return True
        # Explicit human request runs through the FSM only when not already in
        # a deterministic escalation state above. Use the value pre-computed
        # by the chat pipeline — re-running detect_human_request here would
        # add a second LLM call (and on timeout, a second 3s wait).
        return ctx.explicit_human_request

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
        if getattr(chat, "escalation_pre_confirm_pending", False):
            outcome = self._handle_pre_confirm(ctx)
            if outcome is not None:
                return outcome
            # Pre-confirm state was inconsistent (cleared) — fall through.
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
        """T-3: explicit human request lodged before RAG runs.

        Instead of minting a ticket on the literal first user phrase (which is
        usually a meta-question like "how do I reach a human?"), we set the
        ``escalation_pre_confirm_pending`` flag, persist the trigger/context,
        and emit a single short pre-confirmation reply. The actual ticket is
        created on the next turn by :meth:`_handle_pre_confirm` — using the
        substantive content the user is about to provide as the primary
        question of the ticket.
        """
        _svc = _svc_lookup()
        chat = ctx.chat
        if ctx.trace is not None:
            human_request_span = ctx.trace.span(
                name="human-request-detection",
                input={"question": ctx.redacted_question},
            )
            human_request_span.end(output={"matched": True, "pre_confirm": True})
        try:
            set_pre_confirm_state(
                chat,
                trigger=EscalationTrigger.user_request,
                primary_question=ctx.question,
            )
            msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
            out = _svc.complete_escalation_openai_turn(
                phase=EscalationPhase.pre_confirm,
                chat_messages=msgs,
                fact_json=_pre_confirm_fact_json(ctx, trigger=EscalationTrigger.user_request),
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
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
            _svc._persist_turn(
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
            if ctx.trace is not None:
                ctx.trace.update(
                    output={"answer": out.message_to_user, "source": "pre_confirm_ask"},
                    metadata={
                        "chat_ended": False,
                        "escalated": False,
                        "pre_confirm": True,
                        "response_language": ctx.language_context.response_language,
                        "escalation_language": ctx.language_context.escalation_language,
                    },
                )
            return ChatTurnOutcome(
                text=out.message_to_user,
                document_ids=[],
                tokens_used=out.tokens_used,
                chat_ended=False,
                ticket_number=None,
            )
        except Exception as e:
            # Legacy behaviour: log and fall back to the RAG handler so the
            # user still gets a response. Returning None signals the router to
            # try the next handler (RagHandler).
            logger.warning("Escalation T-3 pre-confirm failed, falling back to RAG: %s", e)
            # Reset the flag we tentatively set so the chat does not get
            # stuck in pre_confirm without an actual outgoing question.
            clear_pre_confirm_state(chat)
            ctx.db.add(chat)
            ctx.db.commit()
            return None

    def _handle_pre_confirm(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        """Second turn after T-3: classify user reply and either escalate or
        let the chat continue normally.

        Returns ``None`` only when the pre-confirm context is inconsistent
        (missing payload) — caller falls through to the explicit-request gate.
        """
        _svc = _svc_lookup()
        chat = ctx.chat
        pre_ctx = getattr(chat, "escalation_pre_confirm_context", None) or None
        if not isinstance(pre_ctx, dict):
            # Inconsistent state — clear and let the caller decide what to do.
            clear_pre_confirm_state(chat)
            ctx.db.add(chat)
            ctx.db.commit()
            return None

        stored_trigger = EscalationTrigger.user_request
        try:
            stored_trigger = EscalationTrigger(pre_ctx.get("trigger") or "user_request")
        except ValueError:
            stored_trigger = EscalationTrigger.user_request
        stored_primary_question = (pre_ctx.get("primary_question") or "").strip()

        span = (
            ctx.trace.span(
                name="escalation-pre-confirm",
                input={"clarify_round": int(_pre_confirm_clarify_already_asked(chat))},
            )
            if ctx.trace is not None
            else None
        )

        msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
        try:
            classifier_out = _svc.complete_escalation_openai_turn(
                phase=EscalationPhase.pre_confirm,
                chat_messages=msgs,
                fact_json={
                    **_pre_confirm_fact_json(ctx, trigger=stored_trigger),
                    "clarify_round": 1 if _pre_confirm_clarify_already_asked(chat) else 0,
                },
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
        except Exception as exc:
            if span is not None:
                span.end(output={"error": True}, level="ERROR", status_message=str(exc))
            raise

        decision = classifier_out.followup_decision  # "yes" | "no" | "unclear" | None

        # Promote ambiguous answers to "yes" once we've already asked once;
        # otherwise we'd loop forever asking the user to confirm.
        if decision in (None, "unclear") and _pre_confirm_clarify_already_asked(chat):
            decision = "yes"

        if decision == "no":
            # User declined the handoff. Clear state and let the RAG handler
            # answer the user's current message instead.
            clear_pre_confirm_state(chat)
            ctx.db.add(chat)
            ctx.db.commit()
            if span is not None:
                span.end(output={"decision": "no", "escalated": False, "fallthrough": True})
            return None

        if decision == "unclear":
            # First ambiguous reply — re-ask the confirmation question once.
            _set_pre_confirm_clarify_flag(chat)
            ctx.db.add(chat)
            ctx.db.commit()
            _svc._persist_turn(
                ctx.db,
                chat,
                ctx.tenant_id,
                ctx.question,
                classifier_out.message_to_user,
                [],
                classifier_out.tokens_used,
                optional_entity_types=ctx.optional_entity_types,
                trace=ctx.trace,
            )
            if span is not None:
                span.end(output={"decision": "unclear", "clarify_round": 1})
            if ctx.trace is not None:
                ctx.trace.update(
                    output={
                        "answer": classifier_out.message_to_user,
                        "source": "pre_confirm_clarify",
                    },
                    metadata={
                        "chat_ended": False,
                        "escalated": False,
                        "pre_confirm": True,
                        "response_language": ctx.language_context.response_language,
                        "escalation_language": ctx.language_context.escalation_language,
                    },
                )
            return ChatTurnOutcome(
                text=classifier_out.message_to_user,
                document_ids=[],
                tokens_used=classifier_out.tokens_used,
                chat_ended=False,
                ticket_number=None,
            )

        # decision in {"yes", None (treated as yes after fallthrough above)} —
        # the user is confirming, or has supplied substantive content we treat
        # as implicit confirmation. Create the ticket now.
        # Pick the most descriptive primary_question:
        #   - if the user wrote something substantive this turn, prefer it
        #     (e.g. "сайт не открывается" — that IS the support request);
        #   - else fall back to the original stored question (typical "yes"
        #     short confirmation where the user is just acknowledging).
        current_text = ctx.question.strip()
        looks_substantive = len(current_text) >= 8 and current_text.lower() not in {
            "да",
            "ага",
            "конечно",
            "yes",
            "yep",
            "sure",
            "ok",
            "okay",
            "please",
        }
        primary_question = (
            ctx.question
            if looks_substantive or not stored_primary_question
            else stored_primary_question
        )

        clear_pre_confirm_state(chat)
        try:
            ticket = _svc.create_escalation_ticket(
                ctx.tenant_id,
                primary_question,
                stored_trigger,
                ctx.db,
                chat_id=chat.id,
                session_id=ctx.session_id,
                user_context=ctx.effective_user_ctx,
                optional_entity_types=ctx.optional_entity_types,
            )
        except Exception as exc:
            if span is not None:
                span.end(output={"error": True}, level="ERROR", status_message=str(exc))
            raise

        phase = (
            EscalationPhase.handoff_ask_email
            if not ticket.user_email
            else EscalationPhase.handoff_email_known
        )
        out = _svc.complete_escalation_openai_turn(
            phase=phase,
            chat_messages=_svc.build_chat_messages_for_openai(chat, ctx.redacted_question),
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
            out.tokens_used + classifier_out.tokens_used,
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
        if span is not None:
            span.end(output={"decision": "yes", "escalated": True, "ticket_id": str(ticket.id)})
        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": out.message_to_user, "source": "pre_confirm_handoff"},
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
            escalation_trigger=stored_trigger.value,
            plan_tier=(ctx.effective_user_ctx or {}).get("plan_tier"),
            priority=ticket.priority,
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
            tokens_used=out.tokens_used + classifier_out.tokens_used,
            chat_ended=False,
            ticket_number=ticket.ticket_number,
        )


def _pre_confirm_fact_json(
    ctx: HandlerContext,
    *,
    trigger: EscalationTrigger,
    sla_hours: int = 24,
) -> dict[str, Any]:
    """Fact block for the pre_confirm LLM phase — no ticket exists yet."""
    user_ctx = ctx.effective_user_ctx or {}
    chat_ctx = (ctx.chat.user_context or {}) if ctx.chat else {}
    locale = (
        user_ctx.get("locale")
        or user_ctx.get("browser_locale")
        or chat_ctx.get("locale")
        or chat_ctx.get("browser_locale")
    )
    return {
        "ticket_number": None,
        "sla_hours": sla_hours,
        "user_email": user_ctx.get("email"),
        "trigger": trigger.value,
        "priority": None,
        "locale": locale,
    }
