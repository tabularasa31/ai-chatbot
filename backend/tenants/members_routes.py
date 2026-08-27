"""Team member management — owner-only.

Four routes: invite, list, change role, remove. All of them mounted before
``tenants_router`` so ``/tenants/members`` is never swallowed by that router's
``/{tenant_id}`` catch-all.

Every handler resolves the workspace from the caller, never from the request
body or path, so there is no tenant id to tamper with. Member lookups are
scoped to that workspace, so a user id from another one answers 404.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.auth.middleware import require_owner
from backend.core.config import settings
from backend.core.db import get_db
from backend.core.limiter import limiter, owner_jwt_rate_limit_key
from backend.email.service import send_email
from backend.models import Tenant, User
from backend.tenants.members_service import (
    change_member_role,
    invite_member,
    list_members,
    remove_member,
)
from backend.tenants.schemas import (
    InviteMemberRequest,
    InviteMemberResponse,
    TenantMemberListResponse,
    TenantMemberResponse,
    UpdateMemberRoleRequest,
)

logger = logging.getLogger(__name__)

members_router = APIRouter(prefix="/tenants/members", tags=["members"])


def _member_to_response(member: User) -> TenantMemberResponse:
    return TenantMemberResponse(
        id=member.id,
        email=member.email,
        role=member.role,
        # Unverified + already in a workspace = invite not accepted yet.
        status="active" if member.is_verified else "pending",
        created_at=member.created_at,
    )


def _tenant_id(current_user: User) -> uuid.UUID:
    # ``require_owner`` already refused a principal without a workspace.
    return current_user.tenant_id  # type: ignore[return-value]


def _workspace_name(tenant_id: uuid.UUID, db: Session) -> str:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return tenant.name if tenant else "your team"


def _send_invite_email(
    *, to: str, workspace: str, inviter_email: str, token: str | None
) -> None:
    """Tell the invitee they are in, and how to get in.

    Failures are logged, never raised: the membership is already committed,
    and an owner can re-invite to send another link.
    """
    if token:
        subject = f"You've been invited to {workspace} on Chat9"
        body_text = (
            "Hi,\n\n"
            f"{inviter_email} invited you to join {workspace} on Chat9.\n\n"
            "Set your password and get started:\n\n"
            f"{settings.FRONTEND_URL}/accept-invite?token={token}\n\n"
            "This link expires in 7 days.\n\n"
            "If you weren't expecting this, you can ignore this email.\n"
        )
    else:
        subject = f"You've been added to {workspace} on Chat9"
        body_text = (
            "Hi,\n\n"
            f"{inviter_email} added you to {workspace} on Chat9.\n\n"
            f"Sign in with your existing password: {settings.FRONTEND_URL}/login\n\n"
            "If you weren't expecting this, you can ignore this email.\n"
        )
    try:
        send_email(to=to, subject=subject, body=body_text)
    except Exception as exc:  # pragma: no cover - transport failure
        logger.warning("Failed to send invite email: %s", exc)


@members_router.get("", response_model=TenantMemberListResponse)
def list_members_route(
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberListResponse:
    """Everyone in the workspace, with their role and invite status."""
    members = list_members(_tenant_id(current_user), db)
    return TenantMemberListResponse(
        items=[_member_to_response(m) for m in members]
    )


@members_router.post("/invite", response_model=InviteMemberResponse, status_code=201)
@limiter.limit("30/hour", key_func=owner_jwt_rate_limit_key)
def invite_member_route(
    request: Request,
    body: InviteMemberRequest,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> InviteMemberResponse:
    """Invite someone by e-mail.

    409 when the address already belongs to a member of this workspace or to
    another workspace. Re-inviting someone whose invite is still outstanding
    succeeds and re-issues the link.
    """
    tenant_id = _tenant_id(current_user)
    member, token = invite_member(
        tenant_id=tenant_id,
        email=str(body.email),
        role=body.role,
        db=db,
    )
    _send_invite_email(
        to=member.email,
        workspace=_workspace_name(tenant_id, db),
        inviter_email=current_user.email,
        token=token,
    )
    return InviteMemberResponse(
        member=_member_to_response(member), invite_sent=token is not None
    )


@members_router.patch("/{member_id}", response_model=TenantMemberResponse)
def update_member_role_route(
    member_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberResponse:
    """Change a member's role. The last owner cannot be demoted."""
    member = change_member_role(
        tenant_id=_tenant_id(current_user),
        member_id=member_id,
        role=body.role,
        db=db,
    )
    return _member_to_response(member)


@members_router.delete("/{member_id}", status_code=204, response_model=None)
def remove_member_route(
    member_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Remove a member. Not yourself, and not the last owner."""
    remove_member(
        tenant_id=_tenant_id(current_user),
        actor_id=current_user.id,
        member_id=member_id,
        db=db,
    )
