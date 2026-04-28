"""Analytics event emitters for the chat pipeline."""

from __future__ import annotations

import logging
import threading
from collections import deque
from time import monotonic
from typing import Any

from backend.chat.decision import Decision
from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Escalation rate monitor — global sliding-window counter.
# Fires a warning + PostHog alert when more than ESCALATION_ALERT_THRESHOLD
# escalations occur within ESCALATION_ALERT_WINDOW_SECONDS.
# ---------------------------------------------------------------------------

_escalation_times: deque[float] = deque()
_escalation_lock = threading.Lock()


def _check_escalation_rate(tenant_public_id: str | None, bot_public_id: str | None) -> None:
    from backend.core.config import settings

    window = float(settings.escalation_alert_window_seconds)
    threshold = int(settings.escalation_alert_threshold)
    now = monotonic()
    cutoff = now - window

    with _escalation_lock:
        # Purge timestamps older than the window
        while _escalation_times and _escalation_times[0] < cutoff:
            _escalation_times.popleft()
        _escalation_times.append(now)
        count = len(_escalation_times)

    if count >= threshold:
        logger.warning(
            "Escalation rate threshold exceeded: %d escalations in %.0f s (threshold=%d)",
            count,
            window,
            threshold,
            extra={"escalation_count": count, "window_seconds": window},
        )
        try:
            capture_event(
                "escalation.rate_exceeded",
                distinct_id=_metrics_distinct_id(bot_public_id, tenant_public_id),
                tenant_id=tenant_public_id,
                bot_id=bot_public_id,
                properties={
                    "escalation_count": count,
                    "window_seconds": window,
                    "threshold": threshold,
                },
                groups={"tenant": tenant_public_id} if tenant_public_id else None,
            )
        except Exception:
            pass


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
    query_script: str | None = None,
    kb_scripts: list[str] | None = None,
    cross_lingual_triggered: bool = False,
    cross_lingual_variants_count: int = 0,
    query_kb_language_match: str | None = None,
    retrieval_used_cross_lingual_variant: bool = False,
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
            "query_script": query_script,
            "kb_scripts": kb_scripts,
            "cross_lingual_triggered": cross_lingual_triggered,
            "cross_lingual_variants_count": cross_lingual_variants_count,
            "query_kb_language_match": query_kb_language_match,
            "retrieval_used_cross_lingual_variant": retrieval_used_cross_lingual_variant,
        }
        if decision is not None:
            props["decision"] = decision.kind.value
            props["decision_reason"] = decision.clarify_reason or decision.escalate_reason or "n/a"
            props["clarify_type"] = decision.clarify_type
            props["clarify_reason"] = decision.clarify_reason
            props["budget_blocked"] = decision.budget_blocked
            # Fall back to escalation_trigger when the decision tree didn't produce a
            # reason (escalation originated from the RAG pipeline's should_escalate).
            props["escalation_reason"] = decision.escalate_reason or (
                escalation_trigger if escalated else None
            )
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
                "reason": escalation_reason,
                "escalation_trigger": escalation_trigger,
            },
            groups={"tenant": tenant_public_id} if tenant_public_id else None,
        )
        _check_escalation_rate(tenant_public_id, bot_public_id)
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
