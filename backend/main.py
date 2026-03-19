"""FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.core.limiter import limiter
from backend.admin.routes import admin_router
from backend.auth.routes import auth_router
from backend.chat.routes import chat_router
from backend.clients.routes import clients_router
from backend.documents.routes import documents_router
from backend.embeddings.routes import embeddings_router
from backend.routes.public import public_router
from backend.routes.widget import widget_router
from backend.search.routes import search_router

app = FastAPI(title="AI Chatbot API", version="0.1.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration
ALLOWED_ORIGINS = [
    x.strip()
    for x in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,https://getchat9.live",
    ).split(",")
    if x.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(auth_router, prefix="/auth")
app.include_router(admin_router)
app.include_router(clients_router, prefix="/clients")
app.include_router(documents_router, prefix="/documents")
app.include_router(embeddings_router, prefix="/embeddings")
app.include_router(search_router, prefix="/search")
app.include_router(chat_router, prefix="/chat")
app.include_router(public_router)
app.include_router(widget_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
