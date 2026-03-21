"""Widget routes for embed functionality.

Session init and chat live on ``backend.routes.widget`` (included from ``main``).
"""

from fastapi import APIRouter

widget_router = APIRouter(tags=["widget"])


@widget_router.get("/health")
def widget_health() -> dict[str, str]:
    """Health check for widget endpoints."""
    return {"status": "ok"}
