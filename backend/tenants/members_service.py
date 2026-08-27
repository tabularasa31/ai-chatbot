"""Team membership: invite, list, change role, remove.

Membership is a property of the user row — ``users.tenant_id`` plus
``users.role`` — not a join table. A person belongs to exactly one workspace,
which is what the existing schema already encodes (``Tenant.members`` is the
reverse of ``User.tenant_id``).

**Nobody joins a workspace without an act of their own.** Inviting writes a
``tenant_invitations`` row and nothing else; ``users.tenant_id`` is assigned in
exactly one place, :func:`accept_invitation`, reached only by following the
link. That is the whole point of a separate row: anything written onto
``users`` at invite time — a tenant id, a role — is access granted before the
person has agreed to anything, and this codebase hands a member the chat
logs, the escalations inbox and transcripts that hold customers' original
wording. An earlier draft assigned ``tenant_id`` outright for an invitee who
already had a usable password, on the grounds that they needed no
set-password link. They did not need the password step; they still needed the
consent step.

**Why a table and not the password-reset token.** For a brand-new account an
invite really is a password reset seen from the other side — prove the
address, set a password — and reusing ``reset_password_token`` was honest.
For an account that already has a password, joining a workspace is not a
credential action at all: nothing about it touches ``password_hash``. Making
one column mean both would also leave "asked, not yet joined" with nowhere to
live, because the state is not a property of the invitee (who may not exist
yet) but of the pair (workspace, address). So both paths go through the same
row, and there is one answer to "who has been asked" instead of two.

Removal detaches rather than deletes. ``users.tenant_id`` is
``ON DELETE SET NULL`` and half the FKs pointing at ``users.id`` are too — one
(``gap_dismissals.dismissed_by``) has no ``ondelete`` at all, so a hard delete
of an active member would be refused by the database. Detaching also keeps
attribution intact: a transcript still names the operator who answered in it.
The role goes back to ``owner`` on the way out, because the column describes a
membership, and the next workspace this account joins or creates is its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OWNER
from backend.core.security import hash_password
from backend.models import Tenant, TenantInvitation, User
from backend.models.base import _utcnow

#: How long an invite link stays usable. Longer than the one hour a password
#: reset gets: a reset is answered by someone already at their keyboard, an
#: invite by a colleague who may be away for the weekend.
INVITE_TOKEN_TTL = timedelta(days=7)


@dataclass(frozen=True)
class AcceptedInvitation:
    """What the invitee gets back after joining — no session, by design.

    Accepting proves control of a mailbox, which is enough to join a
    workspace and (for a brand-new account) to set the first password. It is
    not a reason to hand out a logged-in session, so the caller is sent to the
    sign-in page like a completed password reset.
    """

    user: User
    workspace_name: str
    password_set: bool


def list_members(tenant_id: uuid.UUID, db: Session) -> list[User]:
    """Everyone who has actually joined the workspace, oldest first."""
    return (
        db.query(User)
        .filter(User.tenant_id == tenant_id)
        .order_by(User.created_at.asc())
        .all()
    )


def list_open_invitations(
    tenant_id: uuid.UUID, db: Session
) -> list[TenantInvitation]:
    """Invitations sent and not yet accepted, oldest first.

    Expired ones are included: an owner looking at the team needs to see that
    someone never joined, and the fix is to send the invite again.
    """
    return (
        db.query(TenantInvitation)
        .filter(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.accepted_at.is_(None),
        )
        .order_by(TenantInvitation.created_at.asc())
        .all()
    )


def count_owners(tenant_id: uuid.UUID, db: Session) -> int:
    """How many owners the workspace has. Used by the last-owner guard.

    Counts joined members only. A pending invitation naming an owner is not
    an owner: nobody is behind it yet, so it cannot be the one who keeps the
    workspace administrable.
    """
    return (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.role == ROLE_OWNER)
        .count()
    )


def get_member(tenant_id: uuid.UUID, member_id: uuid.UUID, db: Session) -> User | None:
    """A joined member of this workspace, or ``None``.

    Scoped by ``tenant_id``, so a user id belonging to another workspace is
    indistinguishable from one that does not exist.
    """
    return (
        db.query(User)
        .filter(User.id == member_id, User.tenant_id == tenant_id)
        .first()
    )


def get_open_invitation(
    tenant_id: uuid.UUID, invitation_id: uuid.UUID, db: Session
) -> TenantInvitation | None:
    """An unaccepted invitation of this workspace, or ``None``."""
    return (
        db.query(TenantInvitation)
        .filter(
            TenantInvitation.id == invitation_id,
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.accepted_at.is_(None),
        )
        .first()
    )


def _new_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def invite_member(
    *,
    tenant_id: uuid.UUID,
    inviter_id: uuid.UUID,
    email: str,
    role: str,
    db: Session,
) -> TenantInvitation:
    """Ask someone to join the workspace.

    Writes an invitation and nothing else — no user row, no ``tenant_id``, no
    role anywhere a permission check can see. Whether the invitee already has
    a Chat9 account changes only what the accept page asks them for, never
    whether they are a member before answering.

    Re-inviting the same address re-issues the token and applies the new role,
    so a lost e-mail is fixed by sending it again; the previous link dies.
    """
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user is not None and existing_user.tenant_id == tenant_id:
        raise HTTPException(status_code=409, detail="This person is already a member")
    if existing_user is not None and existing_user.tenant_id is not None:
        raise HTTPException(
            status_code=409,
            detail="This e-mail is already registered to another workspace",
        )

    invitation = (
        db.query(TenantInvitation)
        .filter(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.email == email,
        )
        .first()
    )
    if invitation is None:
        invitation = TenantInvitation(tenant_id=tenant_id, email=email)
        db.add(invitation)
    invitation.role = role
    invitation.token = _new_token()
    # Naive UTC — the column is ``DateTime`` without ``timezone=True`` and the
    # ``>= now`` comparison at redemption must match. See ``models/base._utcnow``.
    invitation.expires_at = _utcnow() + INVITE_TOKEN_TTL
    invitation.accepted_at = None
    invitation.invited_by_user_id = inviter_id
    try:
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - concurrent duplicate invite
        db.rollback()
        raise HTTPException(
            status_code=409, detail="An invitation for this e-mail already exists"
        ) from exc
    db.refresh(invitation)
    return invitation


def find_invitation_by_token(token: str, db: Session) -> TenantInvitation:
    """Resolve a live invitation link, or refuse it.

    One 400 for every way a link can fail — unknown, already used, expired.
    Telling them apart would let a stranger with a guessed token learn that it
    once meant something.
    """
    invitation = (
        db.query(TenantInvitation)
        .filter(
            TenantInvitation.token == token,
            TenantInvitation.accepted_at.is_(None),
            TenantInvitation.expires_at >= _utcnow(),
        )
        .first()
    )
    if invitation is None:
        raise HTTPException(
            status_code=400, detail="This invitation link is invalid or has expired."
        )
    return invitation


def invitation_needs_password(invitation: TenantInvitation, db: Session) -> bool:
    """Whether accepting has to set a first password.

    False for an address that already has an account: joining a workspace is
    not a credential action, and an accept page that asked for a password
    would be asking them to overwrite one they are still using.
    """
    return (
        db.query(User).filter(User.email == invitation.email).first() is None
    )


def workspace_name(tenant_id: uuid.UUID, db: Session) -> str:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return tenant.name if tenant else "your team"


def accept_invitation(
    *, token: str, password: str | None, db: Session
) -> AcceptedInvitation:
    """Join the workspace. The only place ``users.tenant_id`` is ever assigned.

    For an address with no account yet, this creates one from the supplied
    password and marks it verified — following a link sent to that mailbox is
    the same proof ``/auth/verify-email`` asks for. For an address that
    already has an account, the password is left exactly as it was; an
    unverified account is additionally marked verified, since the link proved
    the address that registration never did.
    """
    invitation = find_invitation_by_token(token, db)
    user = db.query(User).filter(User.email == invitation.email).first()

    if user is not None and user.tenant_id is not None:
        # They acquired a workspace between invite and acceptance.
        raise HTTPException(
            status_code=409,
            detail="This account already belongs to a workspace.",
        )

    password_set = False
    if user is None:
        if not password:
            raise HTTPException(
                status_code=400,
                detail="Choose a password to finish setting up your account.",
            )
        user = User(
            email=invitation.email,
            password_hash=hash_password(password),
            is_verified=True,
        )
        db.add(user)
        db.flush()
        password_set = True
    elif password:
        raise HTTPException(
            status_code=400,
            detail="This account already has a password — sign in with it after joining.",
        )
    else:
        # Registered but never verified: the link is proof of the address.
        user.is_verified = True
        user.verification_token = None
        user.verification_expires_at = None

    user.tenant_id = invitation.tenant_id
    user.role = invitation.role
    invitation.accepted_at = _utcnow()
    # Cleared so an accepted link cannot be replayed even before it expires.
    invitation.token = None
    try:
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - concurrent registration
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This e-mail is already registered"
        ) from exc
    db.refresh(user)
    return AcceptedInvitation(
        user=user,
        workspace_name=workspace_name(invitation.tenant_id, db),
        password_set=password_set,
    )


def change_member_role(
    *,
    tenant_id: uuid.UUID,
    member_id: uuid.UUID,
    role: str,
    db: Session,
) -> User | TenantInvitation:
    """Move a member between roles, or re-aim an invitation that is still out.

    Refuses to demote the last owner — a workspace with no owner has nobody
    who can invite, configure, or promote, and no route back.
    """
    member = get_member(tenant_id, member_id, db)
    if member is not None:
        if member.role == role:
            return member
        if member.role == ROLE_OWNER and count_owners(tenant_id, db) <= 1:
            raise HTTPException(
                status_code=400,
                detail="The last owner cannot be demoted. Promote someone else first.",
            )
        member.role = role
        db.commit()
        db.refresh(member)
        return member

    invitation = get_open_invitation(tenant_id, member_id, db)
    if invitation is None:
        raise HTTPException(status_code=404, detail="Member not found")
    invitation.role = role
    db.commit()
    db.refresh(invitation)
    return invitation


def remove_member(
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session,
) -> None:
    """Detach a member, or withdraw an invitation that has not been accepted.

    Refuses self-removal (leaving is not the same act as being removed, and an
    owner who removes themselves by accident has no way back in) and refuses
    the last owner.
    """
    member = get_member(tenant_id, member_id, db)
    if member is not None:
        if member.id == actor_id:
            raise HTTPException(
                status_code=400,
                detail="You cannot remove yourself from the workspace",
            )
        if member.role == ROLE_OWNER and count_owners(tenant_id, db) <= 1:
            raise HTTPException(
                status_code=400, detail="The last owner cannot be removed"
            )
        member.tenant_id = None
        member.role = ROLE_OWNER
        db.commit()
        return

    invitation = get_open_invitation(tenant_id, member_id, db)
    if invitation is None:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(invitation)
    db.commit()
