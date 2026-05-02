"""ARQ worker entrypoint.

Run as ``python -m backend.worker``. Imports every module that registers
queueable jobs (so ``@register_job`` decorators have run) and then hands off
to ARQ's ``run_worker``.

Procfile launches this as a separate dyno from ``web``; both processes share
the same Redis and Postgres but never block each other.
"""

from __future__ import annotations

import importlib
import logging

from arq.worker import run_worker

from backend.core.config import settings
from backend.core.queue import get_worker_settings

logger = logging.getLogger(__name__)

# Modules that contain @register_job decorators. Import them eagerly so the
# registry is populated before WorkerSettings is built. New job modules go in
# this list; ARQ does not auto-discover.
_JOB_MODULES: tuple[str, ...] = (
    "backend.jobs._smoke",
    "backend.jobs.crawl_url",
    "backend.jobs.knowledge_extraction",
)


def _load_job_modules() -> None:
    for module in _JOB_MODULES:
        importlib.import_module(module)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not settings.redis_url:
        raise SystemExit(
            "REDIS_URL is not configured — the ARQ worker cannot start. "
            "Set REDIS_URL in the environment (Railway add-on or local dev)."
        )
    _load_job_modules()
    run_worker(get_worker_settings())


if __name__ == "__main__":
    main()
