"""
JWT authentication middleware.
Uses FastAPI dependency injection — no route registration needed.

Base auth: Depends(get_current_user) — valid JWT only.
Dashboard / tenant APIs should use Depends(require_verified_user) unless a route
must work with a not-yet-verified principal (rare; login/register stay public).
Routes that only one role may reach add Depends(require_owner) (or
Depends(require_member)) on top — see ``require_role`` below.
"""
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OPERATOR, ROLE_OWNER
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


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    """Build a dependency admitting only members holding one of ``roles``.

    Layers on top of :func:`require_verified_user`, so the chain is
    "valid JWT → verified e-mail → member of a workspace → right role".

    Status codes, deliberately:

    * **404** when the principal belongs to no workspace. ``users.tenant_id``
      is nullable — a registered-but-unprovisioned account, or a member whose
      workspace was deleted (the FK is ``ON DELETE SET NULL``). Such a user
      holds no role anywhere, and the value sitting in ``users.role`` is
      meaningless: it defaults to ``owner``, so reading it would silently
      promote a tenant-less account. Every tenant-scoped route in the codebase
      already answers "Tenant not found" for this principal; the dependency
      says the same thing earlier, rather than admitting them to a handler
      that then has no tenant to work with.
    * **403** when the principal is a member but holds the wrong role. Not
      404: the caller is known to belong to this workspace, so refusing them
      hides nothing they could not see from their own dashboard.

    Cross-tenant reads keep returning **404** because this dependency never
    looks at the target resource — it only inspects the caller. A member of
    another workspace passing the role check still meets a tenant-scoped
    lookup (``get_tenant_chat``, ``get_tenant_by_id``, the member lookups
    here) that answers 404 for a row it does not own. Role checks narrow who
    may act; they never widen what is visible.
    """
    allowed = frozenset(roles)

    async def _dependency(
        current_user: User = Depends(require_verified_user),
    ) -> User:
        if current_user.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Owner role required"
                if allowed == {ROLE_OWNER}
                else "Insufficient role for this operation",
            )
        return current_user

    return _dependency


#: Owner-only surfaces: settings, API keys, privacy config, member
#: management, tenant deletion, knowledge-base edits, FAQ publishing.
require_owner = require_role(ROLE_OWNER)

#: Any member of the workspace: the inbox, logs, knowledge-base read views.
require_member = require_role(ROLE_OWNER, ROLE_OPERATOR)


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
