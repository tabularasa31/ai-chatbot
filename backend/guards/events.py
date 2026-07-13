"""Structured logging of guard verdicts to ``guard_events`` + PostHog.

Every guard verdict on the primary chat path is recorded so we can measure our
own false-positive / false-negative rate (how many legitimate questions we
block, how many injections we miss) instead of tuning the guards blind.

Design constraints:

- **Never break chat.** Recording is best-effort: any failure (Redis, DB,
  PostHog) is swallowed with a debug log. The chat turn is already decided by
  the time we record.
- **Off the hot path.** The DB write runs in a fire-and-forget background task
  on its own :class:`AsyncSession`, so it adds no latency to the response and
  never touches the request's session (which the pipeline closes before the
  relevance guard even returns).
- **No message content.** Only a SHA-256 of the trigger evidence is stored,
  never the raw user text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid

from backend.guards.types import Verdict

logger = logging.getLogger(__name__)

# Keep strong references to in-flight background writes so the event loop does
# not garbage-collect a pending task mid-flight (asyncio only holds a weak ref).
_pending: set[asyncio.Task[None]] = set()


def _hash_evidence(evidence: str | None) -> str | None:
    if not evidence:
        return None
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


def _emit_posthog(
    *,
    tenant_id: str,
    kind: str,
    verdict: Verdict,
    latency_ms: float | None,
    cache_hit: bool | None,
) -> None:
    """Fire the ``guard.verdict`` event that backs the 'Guards FP/FN' dashboard."""
    try:
        from backend.observability.metrics import capture_event

        capture_event(
            "guard.verdict",
            distinct_id=tenant_id,
            tenant_id=tenant_id,
            properties={
                "kind": kind,
                "blocked": verdict.blocked,
                "reason": verdict.reason.value,
                "score": verdict.score,
                "cache_hit": cache_hit,
                "latency_ms": latency_ms,
            },
        )
    except Exception:
        logger.debug("guard_event posthog emit failed", exc_info=True)


async def _write_guard_event(
    *,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID | None,
    kind: str,
    verdict: Verdict,
    latency_ms: float | None,
    cache_hit: bool | None,
) -> None:
    from backend.core.db import AsyncSessionLocal
    from backend.models import GuardEvent

    try:
        async with AsyncSessionLocal() as session:
            session.add(
                GuardEvent(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    chat_id=chat_id,
                    kind=kind,
                    blocked=verdict.blocked,
                    reason=verdict.reason.value,
                    score=verdict.score,
                    evidence_hash=_hash_evidence(verdict.evidence),
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                )
            )
            await session.commit()
    except Exception:
        # Telemetry is best-effort — a failed write must never surface to chat.
        logger.debug("guard_event db write failed", exc_info=True)


def record_guard_event(
    *,
    tenant_id: uuid.UUID | str,
    chat_id: uuid.UUID | str | None,
    kind: str,
    verdict: Verdict,
    latency_ms: float | None = None,
    cache_hit: bool | None = None,
) -> None:
    """Record a guard verdict (PostHog now, DB write in the background).

    Fire-and-forget: returns immediately. Safe to call from any point on the
    async chat path. All failures are swallowed.

    ``kind`` is the guard family: ``"injection"`` or ``"relevance"``.
    """
    try:
        tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except (ValueError, TypeError):
        logger.debug("guard_event skipped: bad tenant_id %r", tenant_id)
        return

    cid: uuid.UUID | None = None
    if chat_id is not None:
        try:
            cid = chat_id if isinstance(chat_id, uuid.UUID) else uuid.UUID(str(chat_id))
        except (ValueError, TypeError):
            cid = None

    _emit_posthog(
        tenant_id=str(tid),
        kind=kind,
        verdict=verdict,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
    )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (sync context / tests) — skip the async DB write.
        return
    task = loop.create_task(
        _write_guard_event(
            tenant_id=tid,
            chat_id=cid,
            kind=kind,
            verdict=verdict,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
        )
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)
