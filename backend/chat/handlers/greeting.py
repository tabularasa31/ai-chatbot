"""Greeting handler — bootstrap turn and bare social greetings.

Handles two shapes of turn with a localized greeting instead of running
retrieval / generation:

* Bootstrap — empty input on a brand-new session (the widget-open welcome).
* Bare social turn — a typed message with no actionable request (a greeting,
  thanks, or ack). This is gated on the semantic human-request classifier
  (``message_has_request_content``), NOT on word count: short real questions
  ("price?", "wildcard?") still carry request content and flow to RAG. The
  earlier SmallTalkHandler keyed off word count and so wrongly greeted
  one-word questions — this avoids that class of bug while still keeping bare
  greetings from landing on the "I couldn't find that in the documentation"
  soft reply.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.language import LocalizationResult, generate_greeting_in_language_result
from backend.guards.injection_detector import detect_injection_structural
from backend.models import Tenant, TenantProfile


def _resolve_product_name(
    *,
    tenant: Tenant | None,
    db: Session,
    profile: TenantProfile | None = None,
) -> str:
    """Resolve the user-facing product name.

    If ``profile`` is provided, use it directly — process_chat_message already
    fetched it for language resolution, so re-querying is wasteful. The ``db``
    arg remains for callers that don't have the profile pre-loaded.
    """
    if profile is None and tenant is not None:
        profile = (
            db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant.id).first()
        )
    product_name = (profile.product_name if profile and profile.product_name else None) or (
        tenant.name if tenant and tenant.name else None
    )
    return product_name or "this product"


def _build_greeting_result(
    *,
    product_name: str,
    response_language: str,
    api_key: str,
) -> LocalizationResult:
    fallback_text = (
        f"I'm the {product_name} assistant and can help with documentation, "
        "product setup, integrations, and finding the right information. Ask your question."
    )
    return generate_greeting_in_language_result(
        product_name=product_name,
        target_language=response_language,
        api_key=api_key,
        fallback_text=fallback_text,
    )


class GreetingHandler(PipelineHandler):
    """Greet the user instead of running RAG on a bootstrap or bare social turn.

    * Bootstrap: empty input on a brand-new session. Persists only the
      assistant greeting — storing an empty user-message row would pollute
      analytics and inject a blank turn into the OpenAI transcript. Bootstrap
      detection on the next call relies on ``bool(chat.messages)``.
    * Bare social turn: a typed message the human-request classifier flagged as
      carrying no actionable request. Persists the full user + assistant turn.

    Escalation / closed states are never intercepted here: a bare "yes" / "no"
    / email / ticket-id reply has no request content but must reach the
    EscalationStateMachine, so those turns fall through.
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        # Bootstrap: empty input on a brand-new session.
        if not ctx.question_text:
            return ctx.is_new_session

        # Typed turn: only greet when there is genuinely no request to answer.
        if ctx.message_has_request_content or ctx.explicit_human_request:
            return False

        chat = ctx.chat
        # Defer to the EscalationStateMachine for any active escalation / closed
        # state — a short confirmation reply there carries no request content
        # but is not small talk.
        if (
            chat.ended_at is not None
            or chat.escalation_pre_confirm_pending
            or chat.escalation_awaiting_ticket_id is not None
            or chat.escalation_followup_pending
            or chat.escalation_awaiting_request
        ):
            return False

        # Defense in depth: a structural injection attempt is never small talk.
        if detect_injection_structural(ctx.redacted_question).detected:
            return False

        return True

    async def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        from backend.core.db import run_sync

        return await run_sync(ctx.async_db, lambda sync_db: self._handle_sync(ctx, sync_db))

    def _handle_sync(self, ctx: HandlerContext, sync_db: Session) -> ChatTurnOutcome:
        # Lazy import: service.py imports the router at module load, so importing
        # the persistence helpers at module top would create a cycle.
        from backend.chat.service import (
            _persist_assistant_message_with_response_language,
            _persist_turn_with_response_language,
        )

        ctx.db = sync_db
        greeting = _build_greeting_result(
            product_name=_resolve_product_name(
                tenant=ctx.tenant_row,
                db=sync_db,
                profile=ctx.tenant_profile,
            ),
            response_language=ctx.language_context.response_language,
            api_key=ctx.api_key,
        )

        is_bootstrap = not ctx.question_text
        if is_bootstrap:
            # Empty bootstrap turn: persist only the assistant greeting.
            _persist_assistant_message_with_response_language(
                db=sync_db,
                chat=ctx.chat,
                tenant_id=ctx.tenant_id,
                response_language=ctx.language_context.response_language,
                resolution_reason=ctx.language_context.response_language_resolution_reason,
                assistant_content=greeting.text,
                extra_tokens=greeting.tokens_used,
                optional_entity_types=ctx.optional_entity_types,
                language_context=ctx.language_context,
            )
        else:
            # Typed social turn: persist the real user message and the greeting.
            _persist_turn_with_response_language(
                db=sync_db,
                chat=ctx.chat,
                tenant_id=ctx.tenant_id,
                response_language=ctx.language_context.response_language,
                resolution_reason=ctx.language_context.response_language_resolution_reason,
                user_content=ctx.question,
                assistant_content=greeting.text,
                document_ids=[],
                extra_tokens=greeting.tokens_used,
                optional_entity_types=ctx.optional_entity_types,
                language_context=ctx.language_context,
                trace=ctx.trace,
            )

        if ctx.trace is not None:
            ctx.trace.update(
                output={
                    "answer": greeting.text,
                    "source": "greeting" if is_bootstrap else "greeting_social",
                },
                metadata={
                    "chat_ended": False,
                    "escalated": False,
                    "greeting": True,
                    "greeting_kind": "bootstrap" if is_bootstrap else "social",
                    "response_language": ctx.language_context.response_language,
                },
            )
        return ChatTurnOutcome(
            text=greeting.text,
            document_ids=[],
            tokens_used=greeting.tokens_used,
            chat_ended=False,
        )
