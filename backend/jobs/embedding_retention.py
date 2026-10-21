"""Daily purge of message embeddings past the retention window."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from arq.cron import cron
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.queue import _CRON_JOBS
from backend.models import MessageEmbedding

logger = logging.getLogger(__name__)

_EMBEDDING_RETENTION_BATCH_SIZE = 1000


def run_embedding_retention(
    db: Session, *, batch_size: int = _EMBEDDING_RETENTION_BATCH_SIZE
) -> int:
    """Delete message embeddings older than the retention window.

    Called once daily from the ARQ cron. Deletes in committed batches so the
    purge never holds a long lock on this table; the cutoff is fixed at call
    time so rows written during the run are never eligible and the loop
    always terminates. Returns the number of rows deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(
        days=settings.log_embeddings_retention_days
    )
    condition = MessageEmbedding.last_used_at < cutoff

    total = 0
    while True:
        ids = (
            db.execute(
                select(MessageEmbedding.message_id).where(condition).limit(batch_size)
            )
            .scalars()
            .all()
        )
        if not ids:
            break
        db.execute(
            sa_delete(MessageEmbedding).where(MessageEmbedding.message_id.in_(ids))
        )
        db.commit()
        total += len(ids)
        if len(ids) < batch_size:
            break
    if total:
        logger.info("Retention: deleted %d stale message embeddings", total)
    return total


async def _tick_message_embedding_retention(ctx: dict) -> None:
    """Daily ARQ cron: purge message embeddings past the retention window.

    No Sentry Crons monitor here — the one monitor slot is held by
    ``scheduled_crawl_tick``. Runs the sync batched deletes off the ARQ
    event loop thread; ``asyncio.to_thread`` opens its own DB session so
    the sync ``Session`` is never touched across a thread boundary.
    """
    await asyncio.to_thread(_run_embedding_retention_once)


def _run_embedding_retention_once() -> None:
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        run_embedding_retention(db)
    finally:
        db.close()


message_embedding_retention_cron = cron(
    _tick_message_embedding_retention, hour={4}, minute={5}
)
_CRON_JOBS.append(message_embedding_retention_cron)
