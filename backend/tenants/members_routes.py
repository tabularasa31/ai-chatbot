"""Team member management — owner-only.

Six routes: invite, list, change role, remove, and the two that take and give
back the caller's own operator seat. All of them mounted before
``tenants_router`` so ``/tenants/members`` is never swallowed by that router's
``/{tenant_id}`` catch-all.

**There is no per-member seat control here, deliberately.** Inviting somebody
grants their seat and removing them releases it, so an invited member is
always seated and no route exists that could leave one stranded as a member
who cannot answer. The only account that can hold a workspace membership with
no seat is that workspace's founding owner, who was never invited into it —
which is why the two seat routes below address the caller and nobody else.

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
from backend.core.db import get_db
from backend.core.limiter import limiter, owner_jwt_rate_limit_key
from backend.models import User
from backend.operator.sessions import emit_operator_session_ended
from backend.seats.service import count_seats, grant_seat, release_seat
from backend.tenants.members_service import (
    change_member_role,
    invite_member,
    list_members,
    release_chats_held_by,
    remove_member,
    send_invite_email,
    workspace_name,
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
        seat_granted_at=member.seat_granted_at,
    )


def _tenant_id(current_user: User) -> uuid.UUID:
    # ``require_owner`` already refused a principal without a workspace.
    return current_user.tenant_id  # type: ignore[return-value]


@members_router.get("", response_model=TenantMemberListResponse)
def list_members_route(
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberListResponse:
    """Everyone in the workspace, with their role, invite status and seat."""
    tenant_id = _tenant_id(current_user)
    members = list_members(tenant_id, db)
    return TenantMemberListResponse(
        items=[_member_to_response(m) for m in members],
        seats=count_seats(tenant_id=tenant_id, db=db),
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

    Creates an account that cannot yet be logged into and mails a
    set-password link — following it is the invitee's own act of joining.
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
    send_invite_email(
        to=member.email,
        workspace=workspace_name(tenant_id, db),
        inviter_email=current_user.email,
        token=token,
    )
    return InviteMemberResponse(member=_member_to_response(member))


@members_router.put("/me/seat", response_model=TenantMemberResponse)
def take_own_seat_route(
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberResponse:
    """Take a seat for yourself.

    An owner runs the workspace without a seat and without charge. This is the
    one thing a seat adds for them: answering conversations themselves, from
    the console, with the reply landing in the visitor's transcript. Being the
    owner is not a seat, so nothing grants this automatically.

    Addresses the caller, never a member id: a seat for somebody else comes
    with their invitation. Idempotent — taking a seat you already hold keeps
    the date you took it.
    """
    grant_seat(current_user)
    db.commit()
    db.refresh(current_user)
    return _member_to_response(current_user)


@members_router.delete("/me/seat", response_model=TenantMemberResponse)
def give_up_own_seat_route(
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberResponse:
    """Give your own seat back.

    The counterpart of the route above, and the reason it can exist at all: an
    owner is allowed to hold a workspace membership with no seat, so taking one
    must be undoable. Nobody else's seat is reachable from here — for an
    invited member, giving the seat back is removing them.

    Every conversation you are holding is handed back to the bot first, in the
    same transaction. Without that the seat you just gave up is the seat you
    need to release them: the chat stays ``live`` with you assigned, the bot
    stays muted, and ``/operator/chats/{id}/release`` answers 403 because it is
    behind the seat. The visitor would type into nothing until the sweeper's
    idle release fired, up to an hour later, and in a one-owner workspace
    nobody else could free it.

    Idempotent. It costs you nothing administratively — an owner without a seat
    still runs the whole workspace, and only stops answering from the console.
    """
    closed = release_chats_held_by(current_user, db)
    release_seat(current_user)
    db.commit()
    db.refresh(current_user)
    # After the commit, as in ``remove_member``: the seat is given up either
    # way, and a telemetry failure must not turn that into a 500.
    for stretch in closed:
        emit_operator_session_ended(stretch)  # type: ignore[arg-type]
    return _member_to_response(current_user)


@members_router.patch("/{member_id}", response_model=TenantMemberResponse)
def update_member_role_route(
    member_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    current_user: Annotated[User, Depends(require_owner)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantMemberResponse:
    """Change a member's role.

    The last owner cannot be demoted, and nobody can demote themselves.
    """
    member = change_member_role(
        tenant_id=_tenant_id(current_user),
        actor_id=current_user.id,
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
    """Delete a member's account. Not yourself, and not the last owner.

    Their history keeps their signature — see
    ``members_service._stamp_attribution``.
    """
    remove_member(
        tenant_id=_tenant_id(current_user),
        actor_id=current_user.id,
        member_id=member_id,
        db=db,
    )
