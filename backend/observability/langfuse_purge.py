"""Delete a workspace's Langfuse traces.

Langfuse holds the conversations themselves — a trace's input is the visitor's
question and its generation output is the bot's answer. Self-hosting makes it
our infrastructure; it does not make that content ours to keep after the
workspace it belongs to is gone. So a workspace deletion purges it.

**Retention is not doing this for us.** ``docs/07-observability-rollout.md``
still lists "documented retention/TTL configuration in Langfuse itself" under
Remaining Gaps, and the pinned server line (SDK ``langfuse>=2.60.2,<3``) has no
project-level retention window to configure — that arrived in Langfuse 3. Traces
therefore persist until something deletes them, which is what this module is.
If a retention window is ever configured, this stays correct: it makes the
deletion immediate rather than eventual.

Every trace we emit carries a ``tenant:<uuid>`` tag (see
``backend/chat/service.py`` and ``backend/search/routes.py``), which is the
handle used here. Traces are collected by paging forward and then deleted in
batches, rather than re-reading page 1 until it comes back empty: Langfuse
processes a delete asynchronously, so a just-deleted trace can still be listed
and a "drain page 1" loop would never terminate.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Langfuse's own advice is to lower this if a host struggles with large pages.
_PAGE_SIZE = 100
# Trace ids per delete call.
_DELETE_BATCH = 100
# Bounds the paging loop. At 100 traces a page this covers a million traces,
# which no workspace of ours is anywhere near; the guard exists so a server
# that mispages cannot spin forever inside one job attempt.
_MAX_PAGES = 10_000


def langfuse_purge_configured() -> bool:
    """Whether we have a Langfuse project to delete from at all."""
    return bool(
        settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def tenant_trace_tag(tenant_id: str) -> str:
    """The tag every trace of a workspace carries."""
    return f"tenant:{tenant_id}"


def _build_client() -> Any:
    """A bare async Langfuse API client.

    Deliberately not the tracer singleton in ``observability/service.py``: this
    runs in the ARQ worker, which never calls ``init_observability`` and has no
    reason to start the SDK's background flush threads just to issue a handful
    of REST calls.
    """
    from langfuse.api.client import AsyncFernLangfuse

    return AsyncFernLangfuse(
        base_url=settings.langfuse_host,
        x_langfuse_public_key=settings.langfuse_public_key,
        username=settings.langfuse_public_key,
        password=settings.langfuse_secret_key,
    )


async def delete_traces_for_tenant(tenant_id: str) -> int:
    """Delete every Langfuse trace tagged for ``tenant_id``; return the count.

    Raises on a Langfuse error so the caller's retry policy can take over.
    Idempotent: a second run finds nothing left, and deleting a trace that is
    already gone is not an error.
    """
    if not langfuse_purge_configured():
        logger.info("langfuse_purge_skipped reason=not_configured tenant_id=%s", tenant_id)
        return 0

    client = _build_client()
    tag = tenant_trace_tag(tenant_id)

    trace_ids: list[str] = []
    page = 1
    while page <= _MAX_PAGES:
        response = await client.trace.list(page=page, limit=_PAGE_SIZE, tags=tag)
        batch = list(response.data or [])
        trace_ids.extend(t.id for t in batch)
        if len(batch) < _PAGE_SIZE:
            break
        page += 1

    if not trace_ids:
        logger.info("langfuse_purge_empty tenant_id=%s", tenant_id)
        return 0

    for start in range(0, len(trace_ids), _DELETE_BATCH):
        await client.trace.delete_multiple(
            trace_ids=trace_ids[start : start + _DELETE_BATCH]
        )

    logger.info(
        "langfuse_purge_done tenant_id=%s traces=%d", tenant_id, len(trace_ids)
    )
    return len(trace_ids)
