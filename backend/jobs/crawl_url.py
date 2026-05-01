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

from backend.core import db as core_db
from backend.core.queue import _CRON_JOBS, enqueue, register_job

logger = logging.getLogger(__name__)

_JOB_NAME = "crawl_url_source"
_DEDUP_PREFIX = "crawl:"


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
    """Enqueue a crawl job. Idempotent — dedup key prevents double-queue.

    Returns the ARQ job id, or None if Redis is unavailable (graceful degradation).
    """
    return await enqueue(
        _JOB_NAME,
        str(source_id),
        api_key,
        kind=_JOB_NAME,
        tenant_id=tenant_id,
        payload={"source_id": str(source_id)},
        job_id=f"{_DEDUP_PREFIX}{source_id}",
    )


async def _tick_scheduled_crawls(ctx: dict[str, Any]) -> None:
    """Cron: enqueue all sources whose scheduled crawl is due.

    Also picks up sources stuck in ``queued`` status (Redis was unavailable
    when the HTTP handler tried to enqueue). The dedup key in
    ``enqueue_crawl_for_source`` ensures we never double-enqueue a source
    that already has a live ARQ job.
    """
    from sqlalchemy import select

    from backend.models import SourceStatus, Tenant, UrlSource

    now = dt.datetime.now(dt.UTC)
    enqueued = 0

    async with core_db.AsyncSessionLocal() as db:
        result = await db.execute(
            select(UrlSource).where(
                UrlSource.status != SourceStatus.indexing.value,
                # Either the scheduled time is due …
                # … or the source was queued but never picked up (Redis was down).
                (
                    (UrlSource.next_crawl_at <= now)
                    | (UrlSource.status == SourceStatus.queued.value)
                ),
            )
        )
        sources = result.scalars().all()

        for source in sources:
            tenant = await db.get(Tenant, source.tenant_id)
            if not tenant or not tenant.openai_api_key:
                continue
            job_id = await enqueue_crawl_for_source(
                source_id=source.id,
                api_key=tenant.openai_api_key,
                tenant_id=source.tenant_id,
            )
            if job_id:
                enqueued += 1

    logger.info("scheduled_crawl_tick due=%d enqueued=%d", len(sources), enqueued)


scheduled_crawl_cron = cron(_tick_scheduled_crawls, minute={0, 15, 30, 45})
_CRON_JOBS.append(scheduled_crawl_cron)
