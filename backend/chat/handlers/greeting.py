"""Greeting handler — bootstrap turn for empty new sessions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.language import LocalizationResult, generate_greeting_in_language_result
from backend.models import Tenant, TenantProfile


def _resolve_product_name(*, tenant: Tenant | None, db: Session) -> str:
    profile = (
        db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant.id).first()
        if tenant
        else None
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
    """Bootstrap greeting for empty input on a brand-new chat session.

    Persists only the assistant greeting — storing an empty user-message row
    would pollute analytics and inject a blank turn into the OpenAI transcript.
    Bootstrap detection on the next call relies on bool(chat.messages): the
    persisted greeting alone marks the session as no longer new.
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        return not ctx.question_text and ctx.is_new_session

    def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        # Lazy import: service.py imports the router at module load, so importing
        # the persistence helper at module top would create a cycle.
        from backend.chat.service import _persist_assistant_message_with_response_language

        greeting = _build_greeting_result(
            product_name=_resolve_product_name(tenant=ctx.tenant_row, db=ctx.db),
            response_language=ctx.language_context.response_language,
            api_key=ctx.api_key,
        )
        _persist_assistant_message_with_response_language(
            db=ctx.db,
            chat=ctx.chat,
            tenant_id=ctx.tenant_id,
            response_language=ctx.language_context.response_language,
            resolution_reason=ctx.language_context.response_language_resolution_reason,
            assistant_content=greeting.text,
            extra_tokens=greeting.tokens_used,
            optional_entity_types=ctx.optional_entity_types,
            language_context=ctx.language_context,
        )
        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": greeting.text, "source": "greeting"},
                metadata={
                    "chat_ended": False,
                    "escalated": False,
                    "greeting": True,
                    "response_language": ctx.language_context.response_language,
                },
            )
        return ChatTurnOutcome(
            text=greeting.text,
            document_ids=[],
            tokens_used=greeting.tokens_used,
            chat_ended=False,
        )
