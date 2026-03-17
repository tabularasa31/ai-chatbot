"""JWT verification middleware and dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.core.db import SessionLocal
from backend.auth.service import get_current_user_from_token
from backend.models import User

PUBLIC_PATHS = {"/health", "/auth/register", "/auth/login", "/docs", "/openapi.json", "/redoc"}


def _extract_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return auth_header[7:].strip()


class JWTMiddleware(BaseHTTPMiddleware):
    """
    Middleware that verifies JWT for protected paths.

    Extracts token from Authorization: Bearer <token>, verifies signature and expiration,
    sets current_user in request.state. Returns 401 if token invalid or missing on protected routes.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
            return await call_next(request)

        if request.url.path == "/auth/me":
            token = _extract_bearer_token(request)
            if not token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid Authorization header"},
                )
            db = SessionLocal()
            try:
                user = get_current_user_from_token(token, db)
                request.state.current_user = user
                response = await call_next(request)
                return response
            except HTTPException as e:
                return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
            finally:
                db.close()

        return await call_next(request)


def get_current_user(request: Request) -> User:
    """
    FastAPI dependency for protected routes.

    Returns current_user from request.state (set by JWTMiddleware).
    Raises 401 if not authenticated.
    """
    if not hasattr(request.state, "current_user"):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
        )
    return request.state.current_user
