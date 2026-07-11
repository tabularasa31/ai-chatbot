"""
JWT authentication middleware.
Uses FastAPI dependency injection — no route registration needed.

Base auth: Depends(get_current_user) — valid JWT only.
Dashboard / tenant APIs should use Depends(require_verified_user) unless a route
must work with a not-yet-verified principal (rare; login/register stay public).
"""
import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.rls import clear_tenant_context, set_tenant_context
from backend.core.security import decode_access_token
from backend.models import User

security = HTTPBearer(auto_error=False)

_COOKIE_NAME = "chat9_token"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency for protected routes. Accepts token from Authorization header or httpOnly cookie.
    Usage: current_user: User = Depends(get_current_user)
    """
    raw_token: str | None = None
    if credentials:
        raw_token = credentials.credentials
    else:
        raw_token = request.cookies.get(_COOKIE_NAME)

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    user_id_str = decode_access_token(raw_token)
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if user.tenant_id is not None:
        set_tenant_context(db, user.tenant_id)
    return user


async def require_verified_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure that the current user has verified their email.

    Raises 403 if `is_verified` is False.
    """
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified.",
        )
    return current_user


async def require_admin_user(
    current_user: User = Depends(require_verified_user),
) -> User:
    """Ensure that the current user has admin privileges.

    Keeps the RLS tenant context set by ``get_current_user`` — use this for
    admin endpoints operating within the admin's own tenant. Platform-wide
    endpoints must use ``get_platform_admin_user`` instead.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return current_user


async def get_platform_admin_user(
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> User:
    """Admin dependency for PLATFORM-WIDE endpoints (cross-tenant reads and
    writes: global metrics, PII retention cleanup).

    ``get_current_user`` scopes the request to the admin's own tenant for
    RLS; without this explicit bypass, global queries would silently see only
    that tenant's rows once RLS is enforced. Clearing covers both the current
    transaction and every later one in the request.
    """
    clear_tenant_context(db)
    return current_user
