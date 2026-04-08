"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)

from backend.admin.routes import admin_router
from backend.auth.routes import auth_router
from backend.chat.routes import chat_router
from backend.clients.routes import clients_router
from backend.core.limiter import limiter
from backend.documents.routes import documents_router
from backend.embeddings.routes import embeddings_router
from backend.escalation.routes import escalation_router
from backend.eval.routes import eval_router
from backend.gap_analyzer.routes import gap_analyzer_router
from backend.knowledge.routes import knowledge_router
from backend.observability import init_observability, shutdown_observability
from backend.routes.public import public_router
from backend.routes.widget import widget_router
from backend.search.routes import search_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_observability()
    try:
        yield
    finally:
        shutdown_observability()


app = FastAPI(title="AI Chatbot API", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration
ALLOWED_ORIGINS = [
    x.strip()
    for x in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,https://getchat9.live",
    ).split(",")
    if x.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Browser-Locale"],
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
app.include_router(admin_router)
app.include_router(clients_router, prefix="/clients")
app.include_router(documents_router, prefix="/documents")
app.include_router(embeddings_router, prefix="/embeddings")
app.include_router(search_router, prefix="/search")
app.include_router(chat_router, prefix="/chat")
app.include_router(escalation_router)
app.include_router(gap_analyzer_router, prefix="/gap-analyzer")
app.include_router(knowledge_router)
app.include_router(public_router)
app.include_router(widget_router)
app.include_router(eval_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
