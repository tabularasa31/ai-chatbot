"""FastAPI application entry point."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)
RATE_LIMIT_RETRY_AFTER_FALLBACK_SECONDS = 60

from backend.admin.routes import admin_router
from backend.auth.routes import auth_router
from backend.bots.routes import bots_router
from backend.chat.handlers.rag import shutdown_guard_pool
from backend.chat.routes import chat_router
from backend.core.config import settings
from backend.core.limiter import hash_ip_for_logs, limiter
from backend.documents.routes import documents_router
from backend.embeddings.routes import embeddings_router
from backend.escalation.routes import escalation_router
from backend.eval.routes import eval_router
from backend.gap_analyzer.jobs import request_graceful_shutdown as gap_graceful_shutdown
from backend.gap_analyzer.routes import gap_analyzer_router
from backend.jobs.analyze_chat_logs import shutdown_log_analysis_threads
from backend.jobs.kb_language_snapshot import (
    shutdown_kb_snapshot_thread,
    start_kb_snapshot_daily_thread,
)
from backend.knowledge.routes import knowledge_router
from backend.observability import (
    init_metrics,
    init_observability,
    init_sentry,
    shutdown_metrics,
    shutdown_observability,
    shutdown_sentry,
)
from backend.routes.public import public_router
from backend.search.routes import search_router
from backend.tenants.routes import tenants_router
from backend.widget.routes import widget_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_observability()
    init_metrics()
    init_sentry()
    start_kb_snapshot_daily_thread()
    try:
        yield
    finally:
        shutdown_guard_pool()
        gap_graceful_shutdown()
        shutdown_log_analysis_threads()
        shutdown_kb_snapshot_thread()
        shutdown_metrics()
        shutdown_sentry()
        shutdown_observability()


app = FastAPI(title="AI Chatbot API", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    logger.info(
        "widget_rate_limit_exceeded",
        extra={
            "route": request.url.path,
            "tenant_id": request.query_params.get("tenant_id") or "unknown",
            "ip_hash": hash_ip_for_logs(get_remote_address(request)),
        },
    )
    response = _rate_limit_exceeded_handler(request, exc)
    if response.headers.get("Retry-After") is None:
        retry_after = _retry_after_seconds(request)
        response.headers["Retry-After"] = str(retry_after)
    return response


def _retry_after_seconds(request: Request) -> int:
    current_limit = getattr(request.state, "view_rate_limit", None)
    if current_limit is None:
        return RATE_LIMIT_RETRY_AFTER_FALLBACK_SECONDS
    try:
        limit, args = current_limit
        reset_at, _remaining = limiter.limiter.get_window_stats(limit, *args)
    except Exception:
        return RATE_LIMIT_RETRY_AFTER_FALLBACK_SECONDS
    return max(1, int(reset_at - time.time()) + 1)


app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Browser-Locale"],
)

if settings.allowed_hosts != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions so they flow through CORSMiddleware properly.

    Without this, ServerErrorMiddleware sends the 500 response bypassing
    CORSMiddleware's send wrapper, stripping Access-Control-Allow-Origin headers.
    """
    logger.exception("Unhandled exception: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

app.include_router(auth_router, prefix="/auth")
app.include_router(admin_router, include_in_schema=False)
app.include_router(bots_router)
app.include_router(tenants_router, prefix="/tenants")
app.include_router(documents_router, prefix="/documents")
app.include_router(embeddings_router, prefix="/embeddings", include_in_schema=False)
app.include_router(search_router, prefix="/search", include_in_schema=False)
app.include_router(chat_router, prefix="/chat")
app.include_router(escalation_router)
app.include_router(gap_analyzer_router, prefix="/gap-analyzer")
app.include_router(knowledge_router)
app.include_router(public_router)
app.include_router(widget_router)
app.include_router(eval_router, include_in_schema=False)


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
