"""Greeting handler and the stateless widget welcome.

``GreetingHandler`` answers a bare social turn — a typed message with no
actionable request (a greeting, thanks, or ack) — with a localized greeting
instead of running retrieval / generation. It is gated on the semantic
human-request classifier (``message_has_request_content``), NOT on word count:
short real questions ("price?", "wildcard?") still carry request content and
flow to RAG. The earlier SmallTalkHandler keyed off word count and so wrongly
greeted one-word questions — this avoids that class of bug while still keeping
bare greetings from landing on the "I couldn't find that in the documentation"
soft reply.

``greet_widget_visitor`` renders the widget's opening welcome without a chat,
session or trace behind it.
"""

from __future__ import annotations

import functools
import hashlib

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.chat.answer_cache import _bounded
from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.iso_639_1 import ISO_639_1_CODES
from backend.chat.language import (
    LocalizationResult,
    generate_greeting_in_language_result,
    language_script_tag_of,
)
from backend.chat.persistence import _persist_turn_with_response_language
from backend.core import redis as redis_mod
from backend.core.db import run_sync
from backend.guards.injection_detector import detect_injection_structural
from backend.models import Tenant, TenantProfile, TurnOutcome

WIDGET_GREETING_CACHE_TTL_SECONDS = 7 * 24 * 3600
ENGLISH = "en"


def _resolve_product_name(
    *,
    tenant: Tenant | None,
    db: Session,
    profile: TenantProfile | None = None,
) -> str:
    """Resolve the user-facing product name.

    If ``profile`` is provided, use it directly — async_process_chat_message already
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


async def _build_greeting_result(
    *,
    product_name: str,
    response_language: str,
    api_key: str | None,
) -> LocalizationResult:
    fallback_text = (
        f"I'm the {product_name} assistant and can help with documentation, "
        "product setup, integrations, and finding the right information. Ask your question."
    )
    return await generate_greeting_in_language_result(
        product_name=product_name,
        target_language=response_language,
        api_key=api_key,
        fallback_text=fallback_text,
    )


async def widget_product_name(*, tenant: Tenant, db: AsyncSession) -> str:
    return await run_sync(
        db, lambda sync_db: _resolve_product_name(tenant=tenant, db=sync_db)
    )


def _widget_greeting_cache_key(tenant_id: str, product_name: str, language: str) -> str:
    product_digest = hashlib.sha256(product_name.encode("utf-8")).hexdigest()[:16]
    return f"widget:greeting:{tenant_id}:{product_digest}:{language}"


async def greet_widget_visitor(
    *, tenant_id: str, product_name: str, locale: str | None, api_key: str | None
) -> LocalizationResult:
    """Localized welcome for a visitor who has not written yet; persists nothing.

    English when ``locale`` is absent, its language is not an ISO 639-1 code, or
    the LLM is unavailable. A successful generation is cached in Redis per
    tenant, product name and language (root plus explicit script, no region), so only the first visitor per
    week pays for the LLM call.
    """
    language = language_script_tag_of(locale)
    root = language.split("-", 1)[0] if language else None
    if root not in ISO_639_1_CODES or root == ENGLISH or not api_key:
        return await _build_greeting_result(
            product_name=product_name, response_language=ENGLISH, api_key=api_key
        )

    cache_key = _widget_greeting_cache_key(tenant_id, product_name, language)
    cached = await _bounded(redis_mod.cache_get(cache_key), None)
    if cached:
        return LocalizationResult(text=cached)

    result = await _build_greeting_result(
        product_name=product_name, response_language=language, api_key=api_key
    )
    if result.generated:
        await _bounded(
            redis_mod.cache_set_with_ttl(
                cache_key, result.text, WIDGET_GREETING_CACHE_TTL_SECONDS
            ),
            False,
        )
    return result


class GreetingHandler(PipelineHandler):
    """Greet the user instead of running RAG on a bare social turn.

    A bare social turn is a typed message the human-request classifier flagged
    as carrying no actionable request, emitted only at the *start* of a
    conversation (before any substantive turn). Persists the full user +
    assistant turn.

    Once the chat has substantive content, a bare social turn is never greeted:
    the welcome text is an opener, so re-emitting it mid-dialogue is wrong (and
    the classifier can misread a short answer to the bot's own question as
    no-request-content). Those turns fall through to RAG.

    Escalation / closed states are never intercepted here: a bare "yes" / "no"
    / email / ticket-id reply has no request content but must reach the
    EscalationStateMachine, so those turns fall through.
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        # Only greet when there is genuinely no request to answer.
        if ctx.message_has_request_content or ctx.explicit_human_request:
            return False

        chat = ctx.chat
        # Mid-conversation guard: the social greeting is a conversation *opener*
        # ("Welcome to <product> support! Ask your question"). Once the chat has
        # carried real request content, a bare affirmation / ack ("yes, there
        # is", "thanks") is a follow-up, not an opener — and the human-request
        # classifier is nondeterministic on such short replies, so an answer to
        # the bot's own clarifying question ("да есть") can momentarily read as
        # no-request-content. Greeting here is the recurring "re-greet
        # mid-dialogue" bug. Fall through so the turn reaches the escalation FSM
        # / RAG, which continue the thread with full history.
        if chat.has_substantive_content:
            return False
        # Defer to the EscalationStateMachine for any active escalation state —
        # a short confirmation reply there carries no request content but is
        # not small talk.
        if (
            chat.escalation_pre_confirm_pending
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
        product_name = await run_sync(
            ctx.async_db,
            lambda sync_db: _resolve_product_name(
                tenant=ctx.tenant_row,
                db=sync_db,
                profile=ctx.tenant_profile,
            ),
        )
        # Awaited natively on the event loop: the greeting generator makes an
        # OpenAI call for non-English targets and must not run inside the
        # run_sync greenlet (which lives on the loop thread).
        greeting = await _build_greeting_result(
            product_name=product_name,
            response_language=ctx.language_context.response_language,
            api_key=ctx.api_key,
        )
        return await self._in_sync(
            ctx, functools.partial(self._handle_sync, greeting=greeting)
        )

    def _handle_sync(
        self, ctx: HandlerContext, sync_db: Session, *, greeting: LocalizationResult
    ) -> ChatTurnOutcome:
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
            language_context=ctx.language_context,
            trace=ctx.trace,
            turn_outcome=TurnOutcome.social,
        )

        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": greeting.text, "source": "greeting_social"},
                metadata={
                    "escalated": False,
                    "greeting": True,
                    "greeting_kind": "social",
                    "response_language": ctx.language_context.response_language,
                },
            )
        return ChatTurnOutcome(
            text=greeting.text,
            document_ids=[],
            tokens_used=greeting.tokens_used,
        )
