"""Team member management — owner-only.

Four routes: invite, list, change role, remove. All of them mounted before
``tenants_router`` so ``/tenants/members`` is never swallowed by that router's
``/{tenant_id}`` catch-all.

The list shows joined members and outstanding invitations in one sequence,
because that is the question an owner is asking ("who is on my team, and who
have I asked"). They are different rows in different tables, so the ``id`` on
each line addresses whichever it came from, and PATCH/DELETE dispatch on it:
re-aiming an invitation and changing a member's role are the same gesture on
the screen.

Every handler resolves the workspace from the caller, never from the request
body or path, so there is no tenant id to tamper with. Both lookups are
scoped to that workspace, so an id from another one answers 404.
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
from backend.models import TenantInvitation, User
from backend.tenants.members_service import (
    change_member_role,
    invite_member,
    list_members,
    list_open_invitations,
    remove_member,
    workspace_name,
)
from backend.tenants.schemas import (
    InviteMemberRequest,
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
        status="active",
        created_at=member.created_at,
    )


def _invitation_to_response(invitation: TenantInvitation) -> TenantMemberResponse:
    return TenantMemberResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status="pending",
        created_at=invitation.created_at,
    )


def _to_response(row: User | TenantInvitation) -> TenantMemberResponse:
    if isinstance(row, TenantInvitation):
        return _invitation_to_response(row)
    return _member_to_response(row)


def _tenant_id(current_user: User) -> uuid.UUID:
    # ``require_owner`` already refused a principal without a workspace.
    return current_user.tenant_id  # type: ignore[return-value]


def _send_invite_email(
    *, to: str, workspace: str, inviter_email: str, token: str
) -> None:
    """Tell the invitee they have been asked, and how to answer.

    Sent on every invite without exception: the link is the only way to
    become a member, so an invite that goes unsent is an invite that cannot
    be accepted. Failures are logged, never raised — the invitation row is
    already committed, and an owner fixes a lost e-mail by inviting again.
    """
    subject = f"You've been invited to {workspace} on Chat9"
    body_text = (
        "Hi,\n\n"
        f"{inviter_email} invited you to join {workspace} on Chat9.\n\n"
        "Accept the invitation:\n\n"
        f"{settings.FRONTEND_URL}/accept-invite?token={token}\n\n"
        "This link expires in 7 days. You are not a member until you follow "
        "it.\n\n"
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
    """Everyone in the workspace, plus everyone who has been asked."""
    tenant_id = _tenant_id(current_user)
    items = [_member_to_response(m) for m in list_members(tenant_id, db)]
    items += [
        _invitation_to_response(i) for i in list_open_invitations(tenant_id, db)
    ]
    return TenantMemberListResponse(items=items)


@members_router.post("/invite", response_model=TenantMemberResponse, status_code=201)
@limiter.limit("30/hour", key_func=owner_jwt_rate_limit_key)
def invite_member_route(
    request: Request,
    body: InviteMemberRequest,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberResponse:
    """Invite someone by e-mail.

    Creates an invitation, not a membership: the invitee joins by following
    the link, and until then this grants them nothing. 409 when the address
    already belongs to a member of this workspace or to another workspace.
    Re-inviting an outstanding invitation succeeds and re-issues the link.
    """
    tenant_id = _tenant_id(current_user)
    invitation = invite_member(
        tenant_id=tenant_id,
        inviter_id=current_user.id,
        email=str(body.email),
        role=body.role,
        db=db,
    )
    _send_invite_email(
        to=invitation.email,
        workspace=workspace_name(tenant_id, db),
        inviter_email=current_user.email,
        token=invitation.token or "",
    )
    return _invitation_to_response(invitation)


@members_router.patch("/{member_id}", response_model=TenantMemberResponse)
def update_member_role_route(
    member_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberResponse:
    """Change a member's role, or the role an invitation will grant.

    The last owner cannot be demoted.
    """
    row = change_member_role(
        tenant_id=_tenant_id(current_user),
        member_id=member_id,
        role=body.role,
        db=db,
    )
    return _to_response(row)


@members_router.delete("/{member_id}", status_code=204, response_model=None)
def remove_member_route(
    member_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Remove a member, or withdraw an invitation. Not yourself, not the last owner."""
    remove_member(
        tenant_id=_tenant_id(current_user),
        actor_id=current_user.id,
        member_id=member_id,
        db=db,
    )
