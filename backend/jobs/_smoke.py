"""Smoke job for the ARQ infrastructure.

Lets ``backend.worker`` boot with at least one registered job so the worker
process has something to advertise on startup, and gives ops a trivial way
to verify end-to-end queue health (enqueue → consume → BackgroundJob row
flips to completed).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.core.queue import register_job

logger = logging.getLogger(__name__)


@register_job(name="smoke_ping", max_attempts=1)
async def smoke_ping(_ctx: dict[str, Any]) -> str:
    logger.info("arq_smoke_ping")
    return "pong"
