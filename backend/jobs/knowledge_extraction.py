"""ARQ job for tenant knowledge extraction after document embedding.

Replaces the inline synchronous call in the embed pipeline with a
durable, retry-aware queue job so extraction failures go to retry /
dead-letter instead of being silently swallowed, and the embed pipeline
returns immediately after enqueue.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from backend.core.queue import enqueue, get_main_loop, register_job

logger = logging.getLogger(__name__)

_JOB_NAME = "extract_tenant_knowledge"


@register_job(name=_JOB_NAME, max_attempts=3)
async def extract_tenant_knowledge_job(
    ctx: dict[str, Any], document_id: str, tenant_id: str
) -> None:
    """ARQ job: run sync extraction in a thread pool executor."""
    from backend.core.db import SessionLocal
    from backend.models import Tenant
    from backend.tenant_knowledge.extract_tenant_knowledge import (
        run_extract_client_knowledge_for_document,
    )

    doc_uuid = uuid.UUID(document_id)
    tenant_uuid = uuid.UUID(tenant_id)

    def _run() -> None:
        db = SessionLocal()
        try:
            tenant = db.get(Tenant, tenant_uuid)
            api_key = tenant.openai_api_key if tenant else None
            if not api_key:
                logger.warning(
                    "extract_tenant_knowledge_skipped reason=no_api_key "
                    "document_id=%s tenant_id=%s",
                    document_id,
                    tenant_id,
                )
                return
            run_extract_client_knowledge_for_document(
                document_id=doc_uuid,
                db=db,
                api_key=api_key,
            )
        finally:
            db.close()

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run)


async def enqueue_knowledge_extraction(
    *,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str | None:
    """Enqueue an extraction job. Returns ARQ job id or None when Redis is unavailable."""
    return await enqueue(
        _JOB_NAME,
        str(document_id),
        str(tenant_id),
        kind=_JOB_NAME,
        tenant_id=tenant_id,
        payload={"document_id": str(document_id)},
    )


def enqueue_knowledge_extraction_sync(
    *,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str | None:
    """Sync bridge for callers running in FastAPI's sync thread pool.

    Submits the async enqueue coroutine to the main event loop via
    ``asyncio.run_coroutine_threadsafe`` and waits up to 5 s. Returns None
    if the loop is unavailable or on timeout — callers must treat None as
    graceful degradation (log WARNING, never raise).
    """
    loop = get_main_loop()
    if loop is None or not loop.is_running():
        logger.warning(
            "knowledge_enqueue_sync_skipped reason=no_loop document_id=%s",
            document_id,
        )
        return None
    future = asyncio.run_coroutine_threadsafe(
        enqueue_knowledge_extraction(document_id=document_id, tenant_id=tenant_id),
        loop,
    )
    try:
        return future.result(timeout=5)
    except Exception:
        logger.warning(
            "knowledge_enqueue_sync_failed document_id=%s",
            document_id,
            exc_info=True,
        )
        return None
