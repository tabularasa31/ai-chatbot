"""ARQ job queue: durable, retry-aware background work.

Single entry point for everything that should outlive a request, survive a
deploy, and retry on failure. Wraps the ARQ Redis-backed queue with:

- ``@register_job(name=..., max_attempts=...)`` — decorator that registers a
  coroutine as a queueable job and adds status-row bookkeeping.
- ``enqueue(name, *, kind=..., tenant_id=..., payload=..., **kwargs)`` —
  helper for HTTP handlers and other producers.
- ``WORKER_SETTINGS`` — ARQ ``WorkerSettings`` consumed by ``backend.worker``.

Status rows live in ``background_jobs`` (Postgres) and mirror the ARQ job
lifecycle so the admin UI can show queue state without talking to Redis.

Graceful degradation: when ``REDIS_URL`` is unset, ``enqueue`` logs a warning
and returns ``None`` — the row is not written and no work is scheduled.
Callers must decide whether to fall back (e.g. run inline) or fail loudly.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.worker import func as arq_func
from sqlalchemy import update

from backend.core.config import settings
from backend.core.db import AsyncSessionLocal
from backend.models.jobs import BackgroundJob, BackgroundJobStatus

logger = logging.getLogger(__name__)

JobFunc = Callable[..., Awaitable[Any]]

_REGISTERED: list[Any] = []
_REGISTERED_NAMES: dict[str, JobFunc] = {}


def _redis_settings_or_none() -> RedisSettings | None:
    if not settings.redis_url:
        return None
    return RedisSettings.from_dsn(settings.redis_url)


def register_job(
    *,
    name: str | None = None,
    max_attempts: int = 5,
) -> Callable[[JobFunc], JobFunc]:
    """Register an async function as a queueable job.

    The wrapper updates the ``background_jobs`` status row at start, success,
    and failure. ``max_attempts`` mirrors ARQ's ``max_tries`` for the function.
    Failures are re-raised so ARQ schedules the retry; the final failure flips
    the row to ``dead_letter``.
    """

    def decorator(fn: JobFunc) -> JobFunc:
        job_name = name or fn.__name__

        async def wrapper(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            arq_job_id: str = str(ctx.get("job_id", ""))
            attempt: int = int(ctx.get("job_try", 1))
            await _mark_started(arq_job_id, attempt)
            try:
                result = await fn(ctx, *args, **kwargs)
            except Exception as exc:
                is_final = attempt >= max_attempts
                await _mark_failed(
                    arq_job_id,
                    attempt,
                    str(exc),
                    dead_letter=is_final,
                )
                raise
            await _mark_completed(arq_job_id)
            return result

        wrapper.__name__ = job_name
        wrapper.__qualname__ = job_name
        wrapper.__doc__ = fn.__doc__

        registered = arq_func(wrapper, name=job_name, max_tries=max_attempts)
        _REGISTERED.append(registered)
        _REGISTERED_NAMES[job_name] = wrapper
        return fn

    return decorator


async def enqueue(
    name: str,
    *args: Any,
    kind: str | None = None,
    tenant_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    job_id: str | None = None,
    max_attempts: int = 5,
    **kwargs: Any,
) -> str | None:
    """Schedule a registered job. Returns the ARQ job id, or ``None`` when
    Redis is unavailable.

    ``kind`` defaults to ``name`` and is what the admin UI groups by.
    ``payload`` is for human-readable debugging — pass the inputs you want
    visible in the row, separately from the function args.
    Pass ``job_id`` to deduplicate (ARQ rejects duplicate ids while pending).
    """
    redis_settings = _redis_settings_or_none()
    if redis_settings is None:
        logger.warning("queue_enqueue_skipped name=%s reason=redis_disabled", name)
        return None

    pool: ArqRedis | None = None
    try:
        pool = await create_pool(redis_settings)
        job = await pool.enqueue_job(name, *args, _job_id=job_id, **kwargs)
        if job is None:
            logger.warning(
                "queue_enqueue_duplicate name=%s job_id=%s",
                name,
                job_id,
            )
            return None
        await _record_queued(
            arq_job_id=job.job_id,
            kind=kind or name,
            tenant_id=tenant_id,
            payload=payload or {},
            max_attempts=max_attempts,
        )
        return job.job_id
    except Exception as exc:
        logger.warning("queue_enqueue_failed name=%s: %s", name, exc)
        return None
    finally:
        if pool is not None:
            await pool.aclose()


async def _record_queued(
    *,
    arq_job_id: str,
    kind: str,
    tenant_id: uuid.UUID | None,
    payload: dict[str, Any],
    max_attempts: int,
) -> None:
    async with AsyncSessionLocal() as db:
        row = BackgroundJob(
            arq_job_id=arq_job_id,
            kind=kind,
            tenant_id=tenant_id,
            payload=payload,
            status=BackgroundJobStatus.queued.value,
            max_attempts=max_attempts,
        )
        db.add(row)
        await db.commit()


async def _mark_started(arq_job_id: str, attempt: int) -> None:
    if not arq_job_id:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.arq_job_id == arq_job_id)
            .values(
                status=BackgroundJobStatus.in_progress.value,
                attempt_count=attempt,
                started_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        await db.commit()


async def _mark_completed(arq_job_id: str) -> None:
    if not arq_job_id:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.arq_job_id == arq_job_id)
            .values(
                status=BackgroundJobStatus.completed.value,
                finished_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        await db.commit()


async def _mark_failed(
    arq_job_id: str,
    attempt: int,
    error: str,
    *,
    dead_letter: bool,
) -> None:
    if not arq_job_id:
        return
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    new_status = (
        BackgroundJobStatus.dead_letter.value
        if dead_letter
        else BackgroundJobStatus.failed.value
    )
    async with AsyncSessionLocal() as db:
        values: dict[str, Any] = {
            "status": new_status,
            "attempt_count": attempt,
            "last_error": error[:8000],
            "last_error_at": now,
        }
        if dead_letter:
            values["finished_at"] = now
        await db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.arq_job_id == arq_job_id)
            .values(**values)
        )
        await db.commit()


async def _on_worker_startup(_: dict[str, Any]) -> None:
    logger.info("arq_worker_startup jobs=%d", len(_REGISTERED))


async def _on_worker_shutdown(_: dict[str, Any]) -> None:
    logger.info("arq_worker_shutdown")


def get_worker_settings() -> type:
    """Construct the ARQ ``WorkerSettings`` class from registered jobs.

    Built lazily so the import-side-effect of registering jobs (via
    ``register_job`` decorators discovered when the worker imports its job
    modules) happens before the class is materialised. ``backend.worker``
    is responsible for importing all job modules first.
    """
    return type(
        "WorkerSettings",
        (),
        {
            "functions": list(_REGISTERED),
            "redis_settings": _redis_settings_or_none() or RedisSettings(),
            "on_startup": _on_worker_startup,
            "on_shutdown": _on_worker_shutdown,
            "max_tries": 5,
            "keep_result_seconds": 3600,
        },
    )
