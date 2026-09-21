"""Delete Langfuse traces: a whole workspace's, or everything past retention.

Langfuse holds the conversations themselves — a trace's input is the visitor's
question and its generation output is the bot's answer. Self-hosting makes it
our infrastructure; it does not make that content ours to keep. Two callers
delete through here:

- ``delete_traces_for_tenant`` — a workspace deletion purges its traces at
  once, whatever their age.
- ``delete_traces_older_than`` — the daily retention job
  (``backend/jobs/langfuse_retention.py``). Self-hosted OSS Langfuse has no
  retention window of its own (Enterprise Edition only, and our 2.95.x server
  predates it), so this is the only thing that ever expires a trace.

Every trace we emit carries a ``tenant:<uuid>`` tag (see
``backend/chat/service.py`` and ``backend/search/routes.py``), which is the
handle the workspace purge uses. Traces are collected by paging forward and
then deleted in batches, rather than re-reading page 1 until it comes back
empty: Langfuse processes a delete asynchronously, so a just-deleted trace can
still be listed and a "drain page 1" loop would never terminate.
"""

from __future__ import annotations

import logging
from datetime import datetime
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
# Per-run ceiling for the retention job. Unlike a workspace purge it need not
# finish in one go: whatever is left is still older than the cutoff tomorrow,
# so a backlog drains over successive nightly runs instead of one very long
# one holding the Langfuse host.
_RETENTION_MAX_PAGES = 500


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
    try:
        trace_ids, exhausted = await _collect_trace_ids(
            client, max_pages=_MAX_PAGES, tags=tenant_trace_tag(tenant_id)
        )
        if not exhausted:
            # Deleting only what was collected and reporting success would be a
            # partial purge recorded as a complete one. Fail instead, so the
            # caller retries and somebody sees it.
            raise RuntimeError(
                f"Langfuse paging hit the {_MAX_PAGES}-page cap for {tenant_id}"
            )
        if not trace_ids:
            logger.info("langfuse_purge_empty tenant_id=%s", tenant_id)
            return 0
        await _delete_in_batches(client, trace_ids)
    finally:
        await _aclose(client)

    logger.info(
        "langfuse_purge_done tenant_id=%s traces=%d", tenant_id, len(trace_ids)
    )
    return len(trace_ids)


async def delete_traces_older_than(cutoff: datetime) -> int:
    """Delete every trace whose timestamp is before ``cutoff``; return the count.

    Bounded by ``_RETENTION_MAX_PAGES`` per call: a backlog larger than that is
    deleted across successive runs rather than failing, since nothing collected
    here stops being eligible tomorrow. Raises on a Langfuse error so the cron
    reports it.
    """
    if not langfuse_purge_configured():
        logger.info("langfuse_retention_skipped reason=not_configured")
        return 0

    client = _build_client()
    try:
        trace_ids, exhausted = await _collect_trace_ids(
            client, max_pages=_RETENTION_MAX_PAGES, to_timestamp=cutoff
        )
        await _delete_in_batches(client, trace_ids)
    finally:
        await _aclose(client)

    logger.info(
        "langfuse_retention_done cutoff=%s traces=%d backlog_remaining=%s",
        cutoff.isoformat(),
        len(trace_ids),
        not exhausted,
    )
    return len(trace_ids)


async def _collect_trace_ids(
    client: Any, *, max_pages: int, **filters: Any
) -> tuple[list[str], bool]:
    """Page forward through ``trace.list(**filters)`` collecting ids.

    Returns the ids and whether the listing was exhausted before ``max_pages``.
    """
    trace_ids: list[str] = []
    page = 1
    while page <= max_pages:
        response = await client.trace.list(page=page, limit=_PAGE_SIZE, **filters)
        batch = list(response.data or [])
        trace_ids.extend(t.id for t in batch)
        if len(batch) < _PAGE_SIZE:
            return trace_ids, True
        page += 1
    return trace_ids, False


async def _delete_in_batches(client: Any, trace_ids: list[str]) -> None:
    for start in range(0, len(trace_ids), _DELETE_BATCH):
        await client.trace.delete_multiple(
            trace_ids=trace_ids[start : start + _DELETE_BATCH]
        )


async def _aclose(client: Any) -> None:
    """Close the httpx client the Fern-generated wrapper holds, if reachable.

    The generated client owns an httpx.AsyncClient and exposes no close of its
    own; without this the worker leaks a connection pool per deletion.
    """
    httpx_client = getattr(
        getattr(client, "_client_wrapper", None), "httpx_client", None
    )
    inner = getattr(httpx_client, "httpx_client", httpx_client)
    aclose = getattr(inner, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        logger.debug("langfuse_purge_client_close_failed", exc_info=True)
