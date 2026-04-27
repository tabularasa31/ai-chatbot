"""Analytics event emitters for the chat pipeline."""

from __future__ import annotations

import logging
from typing import Any

from backend.chat.decision import Decision
from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)


def _metrics_distinct_id(bot_public_id: str | None, tenant_public_id: str | None) -> str:
    # Import lazily to avoid a module-load cycle: handlers.rag imports service,
    # which now imports events — keep the heavyweight rag module out of events init.
    from backend.chat.handlers.rag import _metrics_distinct_id as _impl
    return _impl(bot_public_id, tenant_public_id)


def _emit_chat_turn_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    chat_id: str | None,
    strategy: str,
    reject_reason: str | None,
    is_reject: bool,
    escalated: bool,
    identified: bool = False,
    latency_ms: int | None = None,
    retrieval_ms: int = 0,
    llm_ms: int = 0,
    reliability_score: str | None = None,
    best_confidence_score: float | None = None,
    decision: Decision | None = None,
    escalation_trigger: str | None = None,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        props: dict[str, Any] = {
            "chat_id": chat_id,
            "strategy": strategy,
            "reject_reason": reject_reason,
            "is_reject": is_reject,
            "escalated": escalated,
            "identified": identified,
            "latency_ms": latency_ms,
            "retrieval_ms": retrieval_ms,
            "llm_ms": llm_ms,
            "reliability_score": reliability_score,
            "best_confidence_score": best_confidence_score,
            "escalation_trigger": escalation_trigger,
        }
        if decision is not None:
            props["decision"] = decision.kind.value
            props["decision_reason"] = decision.clarify_reason or decision.escalate_reason or "n/a"
            props["clarify_type"] = decision.clarify_type
            props["clarify_reason"] = decision.clarify_reason
            props["budget_blocked"] = decision.budget_blocked
            props["escalation_reason"] = decision.escalate_reason
        capture_event(
            "chat.turn",
            distinct_id=chat_id or _metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties=props,
            groups={"tenant": tenant_public_id} if tenant_public_id else None,
        )
    except Exception:
        logger.warning("Failed to emit chat.turn event", exc_info=True)


def _emit_chat_escalated_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    chat_id: str | None,
    escalation_reason: str,
    escalation_trigger: str | None = None,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        capture_event(
            "chat_escalated",
            distinct_id=chat_id or _metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "chat_id": chat_id,
                "escalation_reason": escalation_reason,
                "escalation_trigger": escalation_trigger,
            },
            groups={"tenant": tenant_public_id} if tenant_public_id else None,
        )
    except Exception:
        logger.warning("Failed to emit chat_escalated event", exc_info=True)


def _emit_ai_generation_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_s: float,
    operation: str,
    http_status: int = 200,
) -> None:
    """Emit a PostHog $ai_generation event for LLM Observability cost tracking."""
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        capture_event(
            "$ai_generation",
            distinct_id=_metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "$ai_provider": "openai",
                "$ai_model": model,
                "$ai_input_tokens": input_tokens,
                "$ai_output_tokens": output_tokens,
                "$ai_total_cost_usd": cost_usd,
                "$ai_latency": latency_s,
                "$ai_http_status": http_status,
                "operation": operation,
            },
        )
    except Exception:
        logger.warning("Failed to emit $ai_generation event", exc_info=True)


def _emit_chat_session_ended_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    chat_id: str | None,
    outcome: str,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        capture_event(
            "chat_session_ended",
            distinct_id=chat_id or _metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "chat_id": chat_id,
                "outcome": outcome,
            },
            groups={"tenant": tenant_public_id} if tenant_public_id else None,
        )
    except Exception:
        logger.warning("Failed to emit chat_session_ended event", exc_info=True)
