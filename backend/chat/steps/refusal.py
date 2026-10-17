"""Refusal step: build guard-reject pipeline results.

Single place that turns a guard verdict into a localized reject reply plus
the terminal :class:`ChatPipelineResult`, and records the ``refusal`` Langfuse
span so every reject path is visible as a discrete step in the trace.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.chat.types import (
    ChatPipelineResult,
    PipelineRun,
    RejectReasonLiteral,
    RetrievalContext,
)
from backend.guards.reject_response import RejectReason, build_reject_response_result

logger = logging.getLogger(__name__)

_REASON_ENUM: dict[RejectReasonLiteral, RejectReason] = {
    "injection": RejectReason.INJECTION_DETECTED,
    "not_relevant": RejectReason.NOT_RELEVANT,
    "low_retrieval": RejectReason.LOW_RETRIEVAL_SCORE,
    "rephrase": RejectReason.REPHRASE_REQUEST,
    "social": RejectReason.SOCIAL,
    "social_question": RejectReason.SOCIAL_QUESTION,
}


async def build_reject_result(
    run: PipelineRun,
    *,
    reject_reason: RejectReasonLiteral,
    use_profile: bool = True,
    retrieval: RetrievalContext | None = None,
    include_question: bool = False,
    tokens_as_output: bool = False,
    extras: dict[str, Any] | None = None,
) -> ChatPipelineResult:
    """Render the localized reject reply and wrap it in a terminal result.

    ``use_profile=False`` keeps the injection path's historical behaviour of
    rendering the refusal without tenant-profile hints (the profile is not
    loaded yet when the injection guard fires). ``include_question`` forwards
    the user question to the reject renderer for the reject variants whose
    template references it. ``tokens_as_output`` mirrors the zero-hits fast
    path, which reports the reject render tokens as output tokens on the
    ``chat.turn`` event.
    """
    span = None
    if run.trace is not None:
        span = run.trace.span(
            name="refusal",
            input={"reject_reason": reject_reason},
        )
    try:
        reject_result = await build_reject_response_result(
            reason=_REASON_ENUM[reject_reason],
            profile=run.state.profile if use_profile else None,
            response_language=run.language_context.response_language,
            api_key=run.api_key,
            **({"question": run.question} if include_question else {}),
        )
    except BaseException as exc:  # incl. CancelledError — never leave the span dangling
        if span is not None:
            span.end(level="ERROR", status_message=str(exc) or type(exc).__name__)
        raise
    if span is not None:
        span.end(
            output={
                "reject_reason": reject_reason,
                "tokens_used": reject_result.tokens_used,
                "response_language": run.language_context.response_language,
            }
        )
    return ChatPipelineResult(
        raw_answer=reject_result.text,
        final_answer=reject_result.text,
        tokens_used=reject_result.tokens_used,
        strategy="guard_reject",
        reject_reason=reject_reason,
        is_reject=True,
        is_faq_direct=False,
        retrieval=retrieval,
        escalation_recommended=False,
        escalation_trigger=None,
        faq_match=run.state.faq_match,
        language_context=run.language_context,
        **({"tokens_output": reject_result.tokens_used} if tokens_as_output else {}),
        **(extras or {}),
    )


def build_pre_confirm_escalation_result(
    run: PipelineRun,
    *,
    message_to_user: str,
    tokens_used: int,
    trigger: Any,
    retrieval: RetrievalContext,
    tokens_as_output: bool = False,
    extras: dict[str, Any] | None = None,
) -> ChatPipelineResult:
    """Terminal result that recommends the pre-confirm escalation handoff.

    Used by the support-complaint guard verdict and the consecutive-zero-hits
    in-domain escalation. The reply text is a fallback the handler may replace
    with the rendered pre-confirm message; the FSM arming itself happens in
    ``RagHandler._handle_sync``.
    """
    return ChatPipelineResult(
        raw_answer=message_to_user,
        final_answer=message_to_user,
        tokens_used=tokens_used,
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        is_faq_direct=False,
        retrieval=retrieval,
        escalation_recommended=True,
        escalation_trigger=trigger,
        faq_match=run.state.faq_match,
        language_context=run.language_context,
        **({"tokens_output": tokens_used} if tokens_as_output else {}),
        **(extras or {}),
    )
