"""Team membership: invite, list, change role, remove.

Membership is a property of the user row — ``users.tenant_id`` plus
``users.role`` — not a join table. A person belongs to exactly one workspace,
which is what the existing schema already encodes (``Tenant.members`` is the
reverse of ``User.tenant_id``).

**The invite token is the password-reset token.** An invite and a reset are
the same operation seen from two sides: prove you own this address, then set a
password. Reusing ``reset_password_token`` / ``reset_password_expires_at``
means one expiry rule, one replay rule, and one set-password endpoint instead
of a third token concept that would have to re-derive both. Nothing needs to
tell the two apart at rest:

* the e-mail copy differs because the route that sends it knows what it is;
* the landing page differs because the two links carry different paths
  (``/accept-invite`` vs ``/reset-password``), not different tokens;
* "invited but has not accepted yet" is already derivable — an unaccepted
  invitee is a member (``tenant_id`` set) who is not yet verified, because
  accepting sets ``is_verified``. A self-registered owner is also unverified
  at first, but has no ``tenant_id`` until they verify, so within a tenant's
  member list ``is_verified is False`` means exactly "invite outstanding".

So no column was added. The one thing an invite does differently is live
longer: a colleague may not read their mail within the hour a password reset
allows.

**Removal deletes the account.** There is deliberately no such thing as a
verified user with no workspace: membership and account have the same
lifetime. That is what keeps the invite path honest — every invitee is a new
account setting a first password from the link, so nobody is ever added to a
workspace without an act of their own. Being invited again later means a new
account, with a new id.

Deleting a user would silently erase attribution, because five of the six FKs
into ``users`` are ``ON DELETE SET NULL``: every message the departing person
wrote and every stretch they held would lose its author with no trace that
there had been one, and "who handled this" is asked more often after somebody
leaves, not less. So the account goes and the signature stays —
:func:`_stamp_attribution` writes their e-mail onto the history that points at
them, in the same transaction as the delete.

Stamping happens at deletion rather than at write time. Removal is rare, and
the delete already forces the database to touch every referencing row to apply
``SET NULL`` (``messages.operator_user_id`` carries no index, so that pass is
a scan either way) — the stamp adds a second pass over tables that must be
walked regardless, and costs nothing on the live reply path, where an operator
is answering a waiting visitor. The usual objection to stamping late is that a
partial failure loses exactly what it exists to save; here both the updates
and the delete are flushed into the single request-scoped transaction and
committed once, so the pair is atomic. Should it fail, the rollback leaves the
member in place with their history intact — never an anonymised row.

Not stamped: ``chats.assigned_operator_id`` is live state (who holds this
conversation *now*), not history, and a departed operator holding nothing is
the truth. ``pii_events.actor_user_id`` is never written by anything — the
three surviving directions are all machine egress (``llm_request``,
``escalation_ticket``, ``notification_email``) and neither writer sets it — so
it has no attribution to lose.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OWNER
from backend.core.security import hash_password
from backend.models import GapDismissal, Message, OperatorSession, User
from backend.models.base import _utcnow

#: How long an invite link stays usable. Longer than the one hour a password
#: reset gets: a reset is answered by someone already at their keyboard, an
#: invite by a colleague who may be away for the weekend. Shorter than the
#: e-mail verification window would be too short for the same reason.
INVITE_TOKEN_TTL = timedelta(days=7)


def list_members(tenant_id: uuid.UUID, db: Session) -> list[User]:
    """Every member of the workspace, oldest first."""
    return (
        db.query(User)
        .filter(User.tenant_id == tenant_id)
        .order_by(User.created_at.asc())
        .all()
    )


def count_owners(tenant_id: uuid.UUID, db: Session) -> int:
    """How many owners the workspace has. Used by the last-owner guard."""
    return (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.role == ROLE_OWNER)
        .count()
    )


def get_member(tenant_id: uuid.UUID, member_id: uuid.UUID, db: Session) -> User:
    """Fetch a member of this workspace, or 404.

    Scoped by ``tenant_id``, so a user id belonging to another workspace is
    indistinguishable from one that does not exist.
    """
    member = (
        db.query(User)
        .filter(User.id == member_id, User.tenant_id == tenant_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _issue_invite_token(member: User) -> str:
    token = uuid.uuid4().hex
    member.reset_password_token = token
    # Naive UTC — the column is ``DateTime`` without ``timezone=True`` and the
    # ``>= now`` comparison at redemption must match. See ``models/base._utcnow``.
    member.reset_password_expires_at = _utcnow() + INVITE_TOKEN_TTL
    return token


def invite_member(
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    db: Session,
) -> tuple[User, str]:
    """Add someone to the workspace by e-mail, and return them with a token.

    Three cases only, because an account and its membership have the same
    lifetime — there is no such thing as an existing account with no
    workspace waiting to be adopted:

    * already a member here, invite accepted → 409;
    * belongs to another workspace → 409;
    * otherwise a brand-new account, unverified, with an unusable password,
      which becomes a real login only when the invitee follows the link.

    Re-inviting someone whose invite is still outstanding re-issues the token
    and applies the new role, so a lost e-mail is fixed by sending it again.
    """
    existing = db.query(User).filter(User.email == email).first()

    if existing is not None and existing.tenant_id == tenant_id:
        if existing.is_verified:
            raise HTTPException(
                status_code=409, detail="This person is already a member"
            )
        # Invite outstanding — re-issue it rather than refusing.
        existing.role = role
        token = _issue_invite_token(existing)
        db.commit()
        db.refresh(existing)
        return existing, token

    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="This e-mail is already registered to another workspace",
        )

    member = User(
        email=email,
        # Unusable by construction: a random secret nobody holds, replaced
        # when the invitee sets their own password from the link.
        password_hash=hash_password(uuid.uuid4().hex + uuid.uuid4().hex),
        tenant_id=tenant_id,
        role=role,
        is_verified=False,
    )
    token = _issue_invite_token(member)
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        # Lost a race against a concurrent registration of the same address.
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This e-mail is already registered"
        ) from exc
    db.refresh(member)
    return member, token


def change_member_role(
    *,
    tenant_id: uuid.UUID,
    member_id: uuid.UUID,
    role: str,
    db: Session,
) -> User:
    """Move a member between roles.

    Refuses to demote the last owner — a workspace with no owner has nobody
    who can invite, configure, or promote, and no route back.
    """
    member = get_member(tenant_id, member_id, db)
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


def _stamp_attribution(member: User, db: Session) -> None:
    """Write the departing member's signature onto the history pointing at them.

    Must run in the same transaction as the delete that follows — the caller
    commits once for both. Bulk updates with ``synchronize_session=False``:
    nothing in this request reads those rows afterwards, and the identity map
    is discarded with the session.
    """
    label = member.email
    db.query(Message).filter(Message.operator_user_id == member.id).update(
        {Message.operator_label: label}, synchronize_session=False
    )
    db.query(OperatorSession).filter(
        OperatorSession.operator_user_id == member.id
    ).update({OperatorSession.operator_label: label}, synchronize_session=False)
    db.query(GapDismissal).filter(GapDismissal.dismissed_by == member.id).update(
        {GapDismissal.dismissed_by_label: label}, synchronize_session=False
    )


def remove_member(
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session,
) -> None:
    """Delete a member's account, keeping their signature on the history.

    Refuses self-removal (leaving is not the same act as being removed, and an
    owner who removes themselves by accident has no way back in) and refuses
    the last owner — the guard now protects a delete rather than a detach, so
    what it prevents is a workspace with no owner *and* no way to appoint one.

    The account is gone the moment this commits: any JWT still in the
    departing member's browser stops resolving to a user, so
    ``get_current_user`` answers 401 on their very next request.
    """
    member = get_member(tenant_id, member_id, db)
    if member.id == actor_id:
        raise HTTPException(
            status_code=400, detail="You cannot remove yourself from the workspace"
        )
    if member.role == ROLE_OWNER and count_owners(tenant_id, db) <= 1:
        raise HTTPException(
            status_code=400, detail="The last owner cannot be removed"
        )
    _stamp_attribution(member, db)
    db.delete(member)
    db.commit()
