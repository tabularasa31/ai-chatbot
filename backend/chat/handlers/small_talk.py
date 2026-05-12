"""Small-talk handler — single-word turns that bypass the RAG pipeline.

Greets the user instead of running retrieval / generation when the input is a
single word that isn't a structural injection attempt and the chat is not in
any escalation or closed state.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.handlers.greeting import _build_greeting_result, _resolve_product_name
from backend.guards.injection_detector import detect_injection_structural

# Messages with at most this many words are treated as small talk and short-circuit
# the LLM guards / RAG pipeline. One-word greetings ("hi", "hello") account for
# the vast majority of these inputs.
_SHORT_TURN_MAX_WORDS = 1


class SmallTalkHandler(PipelineHandler):
    """Reply with a greeting to single-word turns outside escalation flows.

    The handler skips itself when the chat is in an escalation or closed state
    so that single-word inputs (yes / no replies, email addresses, ticket id
    confirmations) reach the escalation state machine rather than being eaten
    here.
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        if not ctx.question_text:
            # Empty input is the GreetingHandler's domain (or rejected outright
            # for non-new sessions); never short-circuit it as small talk.
            return False
        chat = ctx.chat
        if (
            chat.escalation_followup_pending
            or chat.escalation_awaiting_ticket_id
            or chat.escalation_pre_confirm_pending
            or chat.ended_at
        ):
            return False
        if len(ctx.redacted_question.split()) > _SHORT_TURN_MAX_WORDS:
            return False
        if detect_injection_structural(ctx.redacted_question).detected:
            return False
        return True

    async def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        from backend.core.db import run_sync

        return await run_sync(ctx.async_db, lambda sync_db: self._handle_sync(ctx, sync_db))

    def _handle_sync(self, ctx: HandlerContext, sync_db: Session) -> ChatTurnOutcome:
        # Lazy import: service.py loads the router at module init, so a top-level
        # import of the persistence helper would create a cycle.
        from backend.chat.service import _persist_turn_with_response_language

        ctx.db = sync_db
        result = _build_greeting_result(
            product_name=_resolve_product_name(
                tenant=ctx.tenant_row,
                db=sync_db,
                profile=ctx.tenant_profile,
            ),
            response_language=ctx.language_context.response_language,
            api_key=ctx.api_key,
        )
        _persist_turn_with_response_language(
            db=sync_db,
            chat=ctx.chat,
            tenant_id=ctx.tenant_id,
            response_language=ctx.language_context.response_language,
            resolution_reason=ctx.language_context.response_language_resolution_reason,
            user_content=ctx.question,
            assistant_content=result.text,
            document_ids=[],
            extra_tokens=result.tokens_used,
            optional_entity_types=ctx.optional_entity_types,
            language_context=ctx.language_context,
        )
        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": result.text, "source": "small_talk"},
                metadata={
                    "chat_ended": False,
                    "escalated": False,
                    "small_talk": True,
                    "question": ctx.redacted_question,
                    "response_language": ctx.language_context.response_language,
                },
            )
        return ChatTurnOutcome(
            text=result.text,
            document_ids=[],
            tokens_used=result.tokens_used,
            chat_ended=False,
        )
