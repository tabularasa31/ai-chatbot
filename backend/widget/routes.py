"""Widget routes for embed functionality."""

from fastapi import APIRouter

widget_router = APIRouter(tags=["widget"])


@widget_router.get("/health")
def widget_health() -> dict[str, str]:
    """Health check for widget endpoints."""
    return {"status": "ok"}
