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

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.util import await_only

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.language import localize_text_to_language_result
from backend.core.config import settings
from backend.escalation.openai_escalation import pre_confirm_fallback_result
from backend.escalation.service import (
    _clear_escalation_clarify_flag,
    _escalation_clarify_already_asked,
    _notify_tenant_ticket_update,
    _set_escalation_clarify_flag,
    advance_notification_marker_to_current,
    apply_collected_contact_email,
    get_latest_escalation_ticket_for_chat,
    parse_contact_email,
)
from backend.models import (
    EscalationPhase,
    EscalationTicket,
    EscalationTrigger,
)
from backend.models.base import _utcnow

# Canonical (English) copy shown when the user asks for a human but has not yet
# stated a forwardable problem. Localized to the user's language at runtime via
# ``localize_text_to_language_result`` — never hardcode translations here. Sets
# the expectation that this chat is not real-time and answers arrive by email,
# then asks for the actual question so support receives a real request rather
# than an empty ticket.
_AWAITING_REQUEST_CANONICAL_TEXT = (
    "Support in this chat doesn't reply in real time — your question is "
    "forwarded to the support team and the answer is sent to your email. "
    "Please describe your question in as much detail as you can, and we'll "
    "pass it on."
)


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
        if chat.escalation_pre_confirm_pending:
            return True
        if chat.escalation_awaiting_request:
            return True
        if chat.escalation_followup_pending:
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
        if chat.escalation_pre_confirm_pending:
            outcome = self._handle_pre_confirm(ctx)
            if outcome is not None:
                return outcome
            # Gate cleared on a substantive non-yes/no reply. Drop into the
            # checks below rather than returning immediately: if that same
            # message is also an explicit human request it must still escalate
            # this turn; otherwise we fall through to RagHandler. Mirrors the
            # vanished-ticket recovery in _handle_awaiting_email above.
        if chat.escalation_awaiting_request:
            outcome = self._handle_awaiting_request(ctx)
            if outcome is not None:
                return outcome
            # No forwardable content yet and not an explicit human request —
            # fall through to RagHandler so an ordinary follow-up still gets a
            # real answer. The awaiting flag stays set until content arrives.
        if chat.escalation_followup_pending:
            outcome = self._handle_followup_yes_no(ctx)
            if outcome is not None:
                return outcome
            # Stale follow-up (the inactivity sweeper reported the session
            # ended) cleared the gate above. Drop into the checks below rather
            # than returning immediately: if the same message is an explicit
            # human request it must still escalate this turn; otherwise we fall
            # through to RagHandler. Mirrors the pre_confirm null-reply recovery.
        # Explicit human request (T-3) — only fires when the user actually
        # asked for a human. Without this gate, a stale-pointer recovery
        # (vanished awaiting-ticket cleared above) would mint a fresh
        # escalation ticket on any ordinary reply, which the legacy inline
        # flow did not do. Escalates immediately (no pre_confirm) only when
        # there is a concrete request to forward; a bare handoff plea with no
        # stated problem first elicits the actual question. Failures propagate
        # rather than degrading to RagHandler once the ticket and support
        # email have been committed.
        if ctx.explicit_human_request:
            if self._has_forwardable_request(ctx):
                return self._handle_explicit_request(ctx)
            return self._enter_awaiting_request(ctx)
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
        out = await_only(
            _svc.complete_escalation_openai_turn(
                phase=EscalationPhase.chat_already_closed,
                chat_messages=msgs,
                fact_json={},
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
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
                apply_collected_contact_email(
                    ticket.id, chat.id, email, ctx.db, latest_user_text=ctx.question
                )
                ctx.db.refresh(ticket)
                ctx.db.refresh(chat)
                ctx.db.expire(chat, ["messages"])
                msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
                out = await_only(
                    _svc.complete_escalation_openai_turn(
                        phase=EscalationPhase.handoff_email_known,
                        chat_messages=msgs,
                        fact_json=_svc.fact_from_ticket(ticket, chat=chat),
                        latest_user_text=ctx.redacted_question,
                        api_key=ctx.api_key,
                        response_language=ctx.language_context.response_language,
                    )
                )
                if awaiting_email_span is not None:
                    awaiting_email_span.end(
                        output={"ticket_found": True, "email_captured": True}
                    )
                outcome = _svc._escalation_turn_response(
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
                # The initial notify above already bundled this turn into the
                # email body via `latest_user_text`. Advance the marker past
                # the just-persisted message so a future follow-up notify
                # doesn't re-send it under the threaded reply.
                try:
                    ctx.db.refresh(ticket)
                    advance_notification_marker_to_current(ticket, ctx.db)
                    ctx.db.commit()
                except Exception as marker_exc:
                    logger.warning(
                        "notify marker advance failed (ticket=%s): %s",
                        ticket.ticket_number,
                        marker_exc,
                    )
                    ctx.db.rollback()
                return outcome
            msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
            out = await_only(
                _svc.complete_escalation_openai_turn(
                    phase=EscalationPhase.email_parse_failed,
                    chat_messages=msgs,
                    fact_json=_svc.fact_from_ticket(ticket, chat=chat),
                    latest_user_text=ctx.redacted_question,
                    api_key=ctx.api_key,
                    response_language=ctx.language_context.response_language,
                )
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

    def _handle_followup_yes_no(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        _svc = _svc_lookup()
        chat = ctx.chat
        # Session-window guard: ``escalation_followup_pending`` is a persistent
        # DB flag, so a user resuming hours later — after the inactivity sweeper
        # reported the session ended (``session_ended_event_at`` set) — would
        # otherwise have a genuine new question classified as a yes/no answer to
        # the long-gone "anything else?" prompt and eaten by the escalation FSM.
        # Treat the follow-up as stale: clear the gate and fall through to RAG so
        # the new question gets a fresh answer. Mirrors the same staleness check
        # the zero-hits rephrase fast path applies in rag.py.
        if chat.session_ended_event_at is not None:
            chat.escalation_followup_pending = False
            _clear_escalation_clarify_flag(chat)
            ctx.db.add(chat)
            ctx.db.commit()
            return None
        followup_span = (
            ctx.trace.span(name="escalation-followup", input={"pending": True})
            if ctx.trace is not None
            else None
        )
        ticket = get_latest_escalation_ticket_for_chat(chat.id, ctx.db)
        msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
        try:
            out = await_only(
                _svc.complete_escalation_openai_turn(
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
                # ``Chat.ended_at`` is ``DateTime`` (naive). asyncpg refuses
                # to coerce aware values into ``TIMESTAMP WITHOUT TIME ZONE``
                # and raises ``DataError`` → ``PendingRollbackError`` on the
                # next attribute access. See ``models/base._utcnow`` for the
                # rationale.
                chat.ended_at = _utcnow()
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
                    session_id=str(chat.session_id) if chat.session_id else None,
                    duration_ms=_svc._session_duration_ms(chat.created_at, chat.ended_at),
                    outcome="resolved",
                )
                return outcome
            # Unclear means the user wrote something other than a bare yes/no —
            # i.e. additional context for the ticket. Forward it to support as
            # a threaded follow-up email. Bare yes/no above is administrative
            # for the bot only; not useful for support and intentionally
            # skipped to avoid noisy update emails.
            _set_escalation_clarify_flag(chat)
            ctx.db.add(chat)
            if followup_span is not None:
                followup_span.end(output={"decision": decision, "chat_ended": False})
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
                chat_ended=False,
                escalated=True,
            )
            try:
                _notify_tenant_ticket_update(ticket, ctx.db)
                ctx.db.commit()
            except Exception as notify_exc:
                logger.warning(
                    "follow-up notify failed (ticket=%s): %s",
                    ticket.ticket_number,
                    notify_exc,
                )
                ctx.db.rollback()
            return outcome
        except Exception as exc:
            if followup_span is not None:
                followup_span.end(
                    output={"error": True},
                    level="ERROR",
                    status_message=str(exc),
                )
            raise

    def _create_ticket_and_handoff(
        self,
        ctx: HandlerContext,
        *,
        pre_confirm_ctx: dict,
        escalation_reason: str,
        trace_source: str,
        extra_tokens: int = 0,
        span: Any | None = None,
        span_output_extra: dict | None = None,
    ) -> ChatTurnOutcome:
        """Create the escalation ticket and render the handoff reply.

        Shared by the pre_confirm "yes" branch and the explicit human-request
        path (which skips confirmation entirely). ``pre_confirm_ctx`` carries
        the canonical ticket question and retrieval context; for the direct
        human-request path it is synthesised on the fly.
        """
        _svc = _svc_lookup()
        chat = ctx.chat
        chat.escalation_pre_confirm_pending = False
        _clear_escalation_clarify_flag(chat)
        # Merge before ticket creation: _notify_tenant_new_ticket inside
        # create_escalation_ticket lazy-loads ticket.chat, which would create a
        # duplicate Chat identity in the session if we don't merge first.
        # Merging here prevents the InvalidRequestError that would silently
        # swallow the email notification.
        chat = ctx.db.merge(chat)
        ctx.chat = chat
        ctx.db.add(chat)
        esc_trigger = EscalationTrigger(
            pre_confirm_ctx.get("trigger", EscalationTrigger.low_similarity.value)
        )
        # Keep `pre_confirm_ctx["primary_question"]` as the canonical ticket
        # question: for low-similarity pre-confirm it holds the actual user
        # query, whereas `ctx.question` on a confirmation turn is typically just
        # the bare "yes". The current turn is surfaced separately via
        # `latest_user_text`, which appends it to the email transcript.
        ticket = _svc.create_escalation_ticket(
            ctx.tenant_id,
            pre_confirm_ctx.get("primary_question") or ctx.question,
            esc_trigger,
            ctx.db,
            chat_id=chat.id,
            session_id=ctx.session_id,
            best_similarity_score=pre_confirm_ctx.get("best_similarity_score"),
            retrieved_chunks=pre_confirm_ctx.get("retrieved_chunks"),
            user_context=ctx.effective_user_ctx,
            optional_entity_types=ctx.optional_entity_types,
            latest_user_text=ctx.question,
        )
        chat.escalation_pre_confirm_context = None
        phase = (
            EscalationPhase.handoff_ask_email
            if not ticket.user_email
            else EscalationPhase.handoff_email_known
        )
        msgs = _svc.build_chat_messages_for_openai(chat, ctx.redacted_question)
        out_handoff = await_only(
            _svc.complete_escalation_openai_turn(
                phase=phase,
                chat_messages=msgs,
                fact_json=_svc.fact_from_ticket(ticket, chat=chat),
                latest_user_text=ctx.redacted_question,
                api_key=ctx.api_key,
                response_language=ctx.language_context.response_language,
            )
        )
        out_handoff.tokens_used += extra_tokens
        if not ticket.user_email:
            chat.escalation_awaiting_ticket_id = ticket.id
        else:
            chat.escalation_followup_pending = True
        ctx.db.add(chat)
        if span is not None:
            span.end(output={**(span_output_extra or {}), "ticket": ticket.ticket_number})
        _svc._emit_chat_escalated_event(
            tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
            bot_public_id=ctx.bot_public_id,
            chat_id=str(chat.id),
            escalation_reason=escalation_reason,
            escalation_trigger=esc_trigger.value,
            plan_tier=(ctx.effective_user_ctx or {}).get("plan_tier"),
            priority=ticket.priority,
        )
        outcome = _svc._escalation_turn_response(
            db=ctx.db,
            chat=chat,
            tenant_id=ctx.tenant_id,
            language_context=ctx.language_context,
            question=ctx.question,
            out=out_handoff,
            optional_entity_types=ctx.optional_entity_types,
            trace=ctx.trace,
            trace_source=trace_source,
            chat_ended=False,
            escalated=True,
            ticket_number=ticket.ticket_number,
        )
        # `create_escalation_ticket` above sent the initial notify with this
        # turn bundled via `latest_user_text`; advance the marker past the
        # just-persisted message to prevent re-send.
        try:
            ctx.db.refresh(ticket)
            advance_notification_marker_to_current(ticket, ctx.db)
            ctx.db.commit()
        except Exception as marker_exc:
            logger.warning(
                "notify marker advance failed (ticket=%s): %s",
                ticket.ticket_number,
                marker_exc,
            )
            ctx.db.rollback()
        return outcome

    def _handle_explicit_request(self, ctx: HandlerContext) -> ChatTurnOutcome:
        """T-3: explicit human request — escalate immediately, no confirmation.

        An explicit "connect me to a human" is itself the confirmation, so the
        pre_confirm gate is skipped here: gating it only loses tickets when the
        user abandons before answering "yes". The pre_confirm step still applies
        to bot-initiated escalations (low_similarity / no_documents), where the
        user has not actually asked for a human.

        Failures propagate rather than falling through to RagHandler, mirroring
        the pre_confirm "yes" branch. ``create_escalation_ticket`` commits the
        ticket and sends the support email as its first side effect, so once we
        are past it, degrading to a plain RAG answer would hide the escalation
        from the user and a retry would mint a duplicate ticket.
        """
        human_request_span = (
            ctx.trace.span(
                name="human-request-detection",
                input={"question": ctx.redacted_question},
            )
            if ctx.trace is not None
            else None
        )
        if human_request_span is not None:
            human_request_span.end(output={"matched": True})
        return self._create_ticket_and_handoff(
            ctx,
            pre_confirm_ctx={
                "trigger": EscalationTrigger.user_request.value,
                "primary_question": ctx.question,
                "best_similarity_score": None,
                "retrieved_chunks": None,
            },
            escalation_reason="explicit_human_request",
            trace_source="escalation_explicit_request",
        )

    # ------------------------------------------------------------------
    # Awaiting-request state — the user asked for a human but hasn't stated a
    # forwardable problem yet. We elicit the actual question instead of minting
    # an empty ticket, then escalate once real content arrives.
    # ------------------------------------------------------------------

    def _has_forwardable_request(self, ctx: HandlerContext) -> bool:
        """Whether there is concrete content worth forwarding to support.

        True when this message states a problem, or when the chat already
        carries substantive content from an earlier turn (the user described
        something, then asked for a human). ``has_substantive_content`` is the
        sticky flag set by the pipeline whenever a turn's
        ``message_has_request_content`` is True — a prior bare greeting never
        sets it, so it does not let an empty ticket through.
        """
        if ctx.message_has_request_content:
            return True
        return bool(ctx.chat.has_substantive_content)

    def _enter_awaiting_request(self, ctx: HandlerContext) -> ChatTurnOutcome:
        """First bare human request with no content — ask for the question."""
        chat = ctx.chat
        chat.escalation_awaiting_request = True
        ctx.db.add(chat)
        return self._emit_awaiting_request_message(
            ctx, trace_source="escalation_awaiting_request"
        )

    def _handle_awaiting_request(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        """Resolve a turn while waiting for the user's actual question.

        - Substantive message → clear the flag and escalate with that content.
        - Still a bare plea for a human → repeat the elicitation message.
        - Anything else (no content, no handoff ask) → clear the flag and
          return None so RagHandler answers the new message afresh.
        """
        chat = ctx.chat
        if ctx.message_has_request_content:
            chat.escalation_awaiting_request = False
            ctx.db.add(chat)
            return self._create_ticket_and_handoff(
                ctx,
                pre_confirm_ctx={
                    "trigger": EscalationTrigger.user_request.value,
                    "primary_question": ctx.question,
                    "best_similarity_score": None,
                    "retrieved_chunks": None,
                },
                escalation_reason="explicit_human_request",
                trace_source="escalation_request_detail_provided",
            )
        if ctx.explicit_human_request:
            return self._emit_awaiting_request_message(
                ctx, trace_source="escalation_awaiting_request_repeat"
            )
        chat.escalation_awaiting_request = False
        ctx.db.add(chat)
        return None

    def _emit_awaiting_request_message(
        self, ctx: HandlerContext, *, trace_source: str
    ) -> ChatTurnOutcome:
        _svc = _svc_lookup()

        # The localization helper is still sync (language.py migration is a
        # separate wave) and makes its own OpenAI call for non-English targets.
        # We run inside a run_sync greenlet ON the event loop thread, so bridge
        # it through a worker thread instead of freezing the loop.
        localized = await_only(
            asyncio.to_thread(
                localize_text_to_language_result,
                canonical_text=_AWAITING_REQUEST_CANONICAL_TEXT,
                target_language=ctx.language_context.response_language,
                api_key=ctx.api_key,
                tenant_id=str(ctx.tenant_id),
                bot_id=str(ctx.bot_id) if ctx.bot_id else None,
                chat_id=str(ctx.chat.id),
            )
        )
        _svc._persist_turn_with_response_language(
            db=ctx.db,
            chat=ctx.chat,
            tenant_id=ctx.tenant_id,
            response_language=ctx.language_context.response_language,
            resolution_reason=ctx.language_context.response_language_resolution_reason,
            user_content=ctx.question,
            assistant_content=localized.text,
            document_ids=[],
            extra_tokens=localized.tokens_used,
            optional_entity_types=ctx.optional_entity_types,
            language_context=ctx.language_context,
        )
        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": localized.text, "source": trace_source},
                metadata={
                    "chat_ended": False,
                    "escalated": False,
                    "awaiting_request": True,
                    "response_language": ctx.language_context.response_language,
                },
            )
        return ChatTurnOutcome(
            text=localized.text,
            document_ids=[],
            tokens_used=localized.tokens_used,
            chat_ended=False,
        )

    def _handle_pre_confirm(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        """Handle a pending pre-confirmation turn.

        Two-stage:
          1. ``classify_pre_confirm_reply`` runs a narrow LLM call that
             returns only the yes/no/unclear/null decision. The classifier
             has no way to emit a user-facing message — that's deliberate;
             it removes the prompt-mixing bug where the LLM combined a
             pre_confirm question with a handoff-style "your request has
             been forwarded" sentence in the same reply.
          2. The decision drives the turn:
             - ``yes``     → existing handoff-phase LLM call (after ticket
               creation), unchanged.
             - ``no``      → static ``declined`` template.
             - ``unclear`` → static ``clarify`` template (re-ask). We never
               auto-promote a repeated ``unclear`` to ``yes``: a support
               ticket only gets created on an explicit user confirmation.
             - ``null``    → the user ignored the handoff question and said
               something substantive (kept describing the problem, or
               switched topic). Treating that as consent would forward
               debugging noise to support, so we clear the pre_confirm gate
               and return ``None`` to fall through to ``RagHandler``, which
               answers the new message afresh and re-offers escalation
               itself if the KB still can't resolve it.
        """
        _svc = _svc_lookup()
        chat = ctx.chat
        pre_confirm_span = (
            ctx.trace.span(name="escalation-pre-confirm", input={"pending": True})
            if ctx.trace is not None
            else None
        )
        pre_confirm_ctx = chat.escalation_pre_confirm_context or {}
        try:
            decision, classify_tokens = await_only(
                _svc.classify_pre_confirm_reply(
                    latest_user_text=ctx.redacted_question,
                    api_key=ctx.api_key,
                )
            )

            if decision == "yes":
                return self._create_ticket_and_handoff(
                    ctx,
                    pre_confirm_ctx=pre_confirm_ctx,
                    escalation_reason="pre_confirm_accepted",
                    trace_source="escalation_pre_confirm_accepted",
                    # Fold the classifier tokens into the per-turn usage so
                    # analytics see the full cost of a pre_confirm→handoff turn.
                    extra_tokens=classify_tokens,
                    span=pre_confirm_span,
                    span_output_extra={"decision": decision},
                )

            if decision == "no":
                chat.escalation_pre_confirm_pending = False
                chat.escalation_pre_confirm_context = None
                _clear_escalation_clarify_flag(chat)
                ctx.db.add(chat)
                if pre_confirm_span is not None:
                    pre_confirm_span.end(output={"decision": decision})
                try:
                    out_declined = await_only(
                        asyncio.wait_for(
                            _svc.render_pre_confirm_text(
                                variant="declined",
                                response_language=ctx.language_context.response_language,
                                api_key=ctx.api_key,
                                tenant_id=str(ctx.tenant_id),
                                bot_id=str(ctx.bot_id) if ctx.bot_id else None,
                                chat_id=str(chat.id),
                            ),
                            timeout=settings.escalation_pre_confirm_render_timeout_seconds,
                        )
                    )
                except TimeoutError:
                    out_declined = pre_confirm_fallback_result("declined")
                out_declined.tokens_used += classify_tokens
                return _svc._escalation_turn_response(
                    db=ctx.db,
                    chat=chat,
                    tenant_id=ctx.tenant_id,
                    language_context=ctx.language_context,
                    question=ctx.question,
                    out=out_declined,
                    optional_entity_types=ctx.optional_entity_types,
                    trace=ctx.trace,
                    trace_source="escalation_pre_confirm_declined",
                    chat_ended=False,
                    escalated=False,
                )

            if decision is None:
                # Substantive non-yes/no reply: the user kept describing their
                # problem (new symptom) or switched topic instead of answering
                # the handoff question. Never read continued debugging as
                # consent — clear the pre_confirm gate and fall through to
                # RagHandler so the new message gets a fresh KB answer (which
                # re-offers escalation itself if it still can't resolve it).
                # Mirrors the vanished-ticket fall-through in
                # _handle_awaiting_email: commit on the sync session so
                # RagHandler observes the cleared flag, then return None.
                chat.escalation_pre_confirm_pending = False
                chat.escalation_pre_confirm_context = None
                _clear_escalation_clarify_flag(chat)
                ctx.db.add(chat)
                ctx.db.commit()
                if pre_confirm_span is not None:
                    pre_confirm_span.end(output={"decision": None, "fell_through": True})
                return None

            # unclear — the user is hesitating or asking a meta-question about
            # the handoff itself. Re-ask; a ticket is only ever created on an
            # explicit "yes", so we never auto-escalate a stuck conversation.
            _set_escalation_clarify_flag(chat)
            ctx.db.add(chat)
            if pre_confirm_span is not None:
                pre_confirm_span.end(output={"decision": decision, "clarify": True})
            try:
                out_clarify = await_only(
                    asyncio.wait_for(
                        _svc.render_pre_confirm_text(
                            variant="clarify",
                            response_language=ctx.language_context.response_language,
                            api_key=ctx.api_key,
                            tenant_id=str(ctx.tenant_id),
                            bot_id=str(ctx.bot_id) if ctx.bot_id else None,
                            chat_id=str(chat.id),
                        ),
                        timeout=settings.escalation_pre_confirm_render_timeout_seconds,
                    )
                )
            except TimeoutError:
                out_clarify = pre_confirm_fallback_result("clarify")
            out_clarify.tokens_used += classify_tokens
            return _svc._escalation_turn_response(
                db=ctx.db,
                chat=chat,
                tenant_id=ctx.tenant_id,
                language_context=ctx.language_context,
                question=ctx.question,
                out=out_clarify,
                optional_entity_types=ctx.optional_entity_types,
                trace=ctx.trace,
                trace_source="escalation_pre_confirm_unclear",
                chat_ended=False,
                escalated=False,
            )
        except Exception as exc:
            if pre_confirm_span is not None:
                pre_confirm_span.end(
                    output={"error": True},
                    level="ERROR",
                    status_message=str(exc),
                )
            raise
