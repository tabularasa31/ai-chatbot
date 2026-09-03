"""API liveness heartbeat for Sentry Crons.

The API process sends an "ok" check-in once a minute to the ``api-heartbeat``
monitor (created on first check-in from ``MONITOR_CONFIG``). Sentry raises a
missed-check-in issue when the process stops reporting — the only signal that
survives a deploy that never reaches ``uvicorn`` (a failing migration in the
start command, a crash-looping container), because Sentry's error capture
lives inside the very process that is not running. Sentry uptime monitors
cannot target the shared ``*.railway.app`` domain.
"""

from __future__ import annotations

import asyncio
import logging

from backend.observability.sentry import capture_cron_checkin

logger = logging.getLogger(__name__)

MONITOR_SLUG = "api-heartbeat"
MONITOR_CONFIG = {
    "schedule": {"type": "interval", "value": 1, "unit": "minute"},
    "checkin_margin": 2,
    "max_runtime": 1,
    "failure_issue_threshold": 3,
    "recovery_threshold": 1,
}
INTERVAL_SECONDS = 60


def send_heartbeat() -> None:
    capture_cron_checkin(
        monitor_slug=MONITOR_SLUG, status="ok", monitor_config=MONITOR_CONFIG
    )


async def _heartbeat_loop() -> None:
    while True:
        try:
            send_heartbeat()
        except Exception:
            logger.debug("api_heartbeat_failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)


def start_api_heartbeat() -> asyncio.Task[None]:
    """Start the heartbeat on the running loop; returns the task to stop later."""
    return asyncio.create_task(_heartbeat_loop(), name="api-heartbeat")


async def stop_api_heartbeat(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
