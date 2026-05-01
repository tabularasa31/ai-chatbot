"""Durable ARQ job for URL source crawling.

Replaces FastAPI BackgroundTasks with a Redis-backed, retry-aware queue so
crawl jobs survive Railway deploys and OOM restarts.

Public API:
  - ``enqueue_crawl_for_source``  — async helper for HTTP handlers
  - ``crawl_url_source_job``      — ARQ job (runs sync crawl in thread)
  - ``scheduled_crawl_cron``      — ARQ cron: enqueues due / stuck-queued sources
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from typing import Any

from arq.cron import cron
from sqlalchemy import select

from backend.core import db as core_db
from backend.core.queue import _CRON_JOBS, enqueue, get_main_loop, register_job

logger = logging.getLogger(__name__)

_JOB_NAME = "crawl_url_source"
_MAX_CRAWL_JOBS_PER_TICK = 100


@register_job(name=_JOB_NAME, max_attempts=3)
async def crawl_url_source_job(ctx: dict[str, Any], source_id: str, api_key: str) -> None:
    """ARQ job wrapper for URL crawl. Offloads sync work to a thread pool."""
    from backend.documents.url_service import crawl_url_source as _sync_crawl

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sync_crawl, uuid.UUID(source_id), api_key)


async def enqueue_crawl_for_source(
    *,
    source_id: uuid.UUID,
    api_key: str,
    tenant_id: uuid.UUID,
) -> str | None:
    """Enqueue a crawl job.

    Each call produces a new ARQ job with a unique id so history is preserved
    in ``background_jobs``. Deduplication is enforced upstream via
    ``UrlSource.status`` (``trigger_refresh`` throttles HTTP callers; the cron
    skips sources that are already ``indexing``).

    Returns the ARQ job id, or None if Redis is unavailable (graceful degradation).
    """
    return await enqueue(
        _JOB_NAME,
        str(source_id),
        api_key,
        kind=_JOB_NAME,
        tenant_id=tenant_id,
        payload={"source_id": str(source_id)},
    )


def enqueue_crawl_for_source_sync(
    *,
    source_id: uuid.UUID,
    api_key: str,
    tenant_id: uuid.UUID,
) -> str | None:
    """Sync bridge for HTTP handlers running in FastAPI's thread pool.

    Submits the async enqueue coroutine to the main event loop via
    ``asyncio.run_coroutine_threadsafe`` and waits up to 5 s for the result.
    Returns None if the loop is unavailable (startup edge case) or on timeout.
    """
    loop = get_main_loop()
    if loop is None or not loop.is_running():
        logger.warning("crawl_enqueue_sync_skipped reason=no_loop source_id=%s", source_id)
        return None
    future = asyncio.run_coroutine_threadsafe(
        enqueue_crawl_for_source(source_id=source_id, api_key=api_key, tenant_id=tenant_id),
        loop,
    )
    try:
        return future.result(timeout=5)
    except Exception:
        logger.warning("crawl_enqueue_sync_failed source_id=%s", source_id, exc_info=True)
        return None


async def _tick_scheduled_crawls(ctx: dict[str, Any]) -> None:
    """Cron: enqueue sources whose scheduled crawl is due or that got stuck in queued.

    Sources stuck in ``queued`` happen when Redis was unavailable during the
    original HTTP enqueue call.  The cron retries them each tick until a worker
    picks them up.
    """
    from backend.models import SourceStatus, Tenant, UrlSource

    now = dt.datetime.now(dt.UTC)

    async with core_db.AsyncSessionLocal() as db:
        result = await db.execute(
            select(UrlSource, Tenant.openai_api_key)
            .join(Tenant, UrlSource.tenant_id == Tenant.id)
            .where(
                Tenant.openai_api_key.isnot(None),
                UrlSource.status != SourceStatus.indexing.value,
                (
                    (UrlSource.next_crawl_at <= now)
                    | (UrlSource.status == SourceStatus.queued.value)
                ),
            )
            .limit(_MAX_CRAWL_JOBS_PER_TICK)
        )
        rows = result.all()

    enqueued = 0
    for source, api_key in rows:
        job_id = await enqueue_crawl_for_source(
            source_id=source.id,
            api_key=api_key,
            tenant_id=source.tenant_id,
        )
        if job_id:
            enqueued += 1

    logger.info("scheduled_crawl_tick due=%d enqueued=%d", len(rows), enqueued)


scheduled_crawl_cron = cron(_tick_scheduled_crawls, minute={0, 15, 30, 45})
_CRON_JOBS.append(scheduled_crawl_cron)
