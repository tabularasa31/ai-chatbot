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

**Inviting grants a seat.** A colleague is invited in order to answer
customers, and a member who cannot answer is not what anyone is asking for —
so the seat comes with the invitation rather than as a second step, and the
removal below releases it. Between those two there is nothing: an invited
member is always seated. The one account that can sit in a workspace without
a seat is its founding owner, who administers without one and takes a seat
only if they also want to answer from the console. See ``backend/seats``.

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

import logging
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OWNER
from backend.core.config import settings
from backend.core.security import hash_password
from backend.email.service import send_email
from backend.models import (
    Chat,
    GapDismissal,
    Message,
    OperatorSession,
    OperatorSessionEndReason,
    OperatorState,
    Tenant,
    TenantApiKey,
    User,
)
from backend.models.base import _utcnow
from backend.operator.sessions import emit_operator_session_ended
from backend.seats.service import grant_seat

#: How long an invite link stays usable. Longer than the one hour a password
#: reset gets: a reset is answered by someone already at their keyboard, an
#: invite by a colleague who may be away for the weekend. Shorter than the
#: e-mail verification window would be too short for the same reason.
INVITE_TOKEN_TTL = timedelta(days=7)


logger = logging.getLogger(__name__)


def workspace_name(tenant_id: uuid.UUID, db: Session) -> str:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return tenant.name if tenant else "your team"


def send_invite_email(
    *, to: str, workspace: str, inviter_email: str | None, token: str
) -> None:
    """Mail the set-password link that turns an invitation into a login.

    Lives here rather than in the route because two callers need it: the
    invite itself, and a pending invitee pressing "Forgot password" — see
    :func:`resend_invite_for_pending_member`. Failures are logged, never
    raised: the membership is already committed, and the fix is another
    invite.
    """
    subject = f"You've been invited to {workspace} on Chat9"
    intro = (
        f"{inviter_email} invited you to join {workspace} on Chat9."
        if inviter_email
        else f"You've been invited to join {workspace} on Chat9."
    )
    body_text = (
        "Hi,\n\n"
        f"{intro}\n\n"
        "Set your password and get started:\n\n"
        f"{settings.FRONTEND_URL}/accept-invite?token={token}\n\n"
        "This link expires in 7 days.\n\n"
        "If you weren't expecting this, you can ignore this email.\n"
    )
    try:
        send_email(to=to, subject=subject, body=body_text)
    except Exception as exc:  # pragma: no cover - transport failure
        logger.warning("Failed to send invite email: %s", exc)


def resend_invite_for_pending_member(email: str, db: Session) -> bool:
    """If this address is an unaccepted invitation, send the invite again.

    The invite token and the password-reset token are the same column, so
    without this the two clobber each other. The realistic path is not
    hypothetical: an invitee whose link expired (or who never found the
    e-mail) cannot log in, so they press "Forgot password" — which used to
    overwrite their invite token and shorten its life to an hour, after which
    the invite link they eventually found reported "invalid or expired". The
    owner's instinct is then to re-invite, and round it goes.

    The two acts are the same wish — *let me in* — so this answers it with the
    thing that does: a fresh invite link on the invite's own seven-day clock.
    Nothing is clobbered because nothing else is in play; a pending invitee has
    no password to reset.

    Returns True when it handled the address, so the caller skips the reset.

    The other direction needs no fix: ``invite_member`` refuses a verified
    member with 409, so a re-invite can never void a live reset token.
    """
    member = db.query(User).filter(User.email == email).first()
    if (
        member is None
        or member.tenant_id is None
        or member.is_verified
    ):
        return False
    token = _issue_invite_token(member)
    db.commit()
    send_invite_email(
        to=member.email,
        workspace=workspace_name(member.tenant_id, db),
        inviter_email=None,
        token=token,
    )
    return True


def list_members(tenant_id: uuid.UUID, db: Session) -> list[User]:
    """Every member of the workspace, oldest first."""
    return (
        db.query(User)
        .filter(User.tenant_id == tenant_id)
        .order_by(User.created_at.asc())
        .all()
    )


def count_owners(tenant_id: uuid.UUID, db: Session) -> int:
    """How many owners can actually administer the workspace.

    ``is_verified`` is the whole point of this filter. A pending invitee is a
    member row with a role and no person behind it, so counting them lets a
    workspace lock itself out for real: invite ``typo@exmaple.com`` as owner,
    the count reads 2, demote yourself, and the only remaining "owner" is a
    link to an address that does not exist. Every owner surface then 403s,
    including the one that could promote someone back, and the invite token
    dies in seven days with nothing left behind it.

    The same reasoning makes the expiry sweep safe: an unaccepted invitation
    can never be what holds this count above zero, so deleting one can never
    strand a workspace.
    """
    return (
        db.query(User)
        .filter(
            User.tenant_id == tenant_id,
            User.role == ROLE_OWNER,
            User.is_verified.is_(True),
        )
        .count()
    )


def _lock_workspace(tenant_id: uuid.UUID, db: Session) -> None:
    """Serialise the owner-count guards on the workspace row.

    ``claim_chat`` gets its atomicity from a conditional UPDATE, because there
    the contended thing *is* the row being written. Here it is an invariant
    across a set of rows — "at least one verified owner remains" — and two
    owners removing each other write to two different rows, so row locks on
    the targets never meet. Both transactions do touch the workspace, so that
    is where they are made to queue: the second one blocks, then re-counts
    after the first has committed and sees 1 rather than 2.

    ``FOR UPDATE`` is a no-op on SQLite, which serialises writers globally
    anyway, so the tests exercise the logic and PostgreSQL supplies the lock.
    """
    db.query(Tenant).filter(Tenant.id == tenant_id).with_for_update().first()


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

    Either way the member ends up seated: the invitation and the seat are one
    action, and the way a seat is released is the removal below.
    """
    existing = db.query(User).filter(User.email == email).first()

    if existing is not None and existing.tenant_id == tenant_id:
        if existing.is_verified:
            raise HTTPException(
                status_code=409, detail="This person is already a member"
            )
        # Invite outstanding — re-issue it rather than refusing.
        existing.role = role
        # Idempotent, and it repairs a row that somehow lost its seat: a
        # pending invitee is on their way in to answer conversations.
        grant_seat(existing)
        token = _issue_invite_token(existing)
        db.commit()
        db.refresh(existing)
        return existing, token

    if existing is not None and existing.tenant_id is not None:
        raise HTTPException(
            status_code=409,
            detail="This e-mail is already registered to another workspace",
        )

    if existing is not None:
        # An account mid-signup: registered, not yet verified, so no workspace
        # was provisioned for them. Rare and transient, and not ours to
        # hijack — saying "another workspace" here would be a lie, since they
        # belong to none.
        raise HTTPException(
            status_code=409,
            detail="This e-mail is already registered. Ask them to finish signing up.",
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
    # The seat is part of the invitation, not a second decision: one action
    # adds the colleague and gives them what they were added to do.
    grant_seat(member)
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
    actor_id: uuid.UUID,
    member_id: uuid.UUID,
    role: str,
    db: Session,
) -> User:
    """Move a member between roles.

    Two refusals, and they are not the same one. **Self-demotion** is refused
    outright, exactly as ``remove_member`` refuses self-removal: dropping your
    own last privilege is the one mistake with no undo, since the surface that
    would restore it is the surface you just left. Promote a successor and let
    them demote you — the same shape succession already has.

    **The last owner** cannot be demoted by anyone, because a workspace with
    no owner has nobody who can invite, configure, or promote, and no route
    back.
    """
    _lock_workspace(tenant_id, db)
    member = get_member(tenant_id, member_id, db)
    if member.role == role:
        return member
    if member.id == actor_id and role != ROLE_OWNER:
        raise HTTPException(
            status_code=400,
            detail=(
                "You cannot demote yourself. Promote another owner and ask "
                "them to change your role."
            ),
        )
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
    db.query(TenantApiKey).filter(
        TenantApiKey.created_by_user_id == member.id
    ).update({TenantApiKey.created_by_label: label}, synchronize_session=False)


def _release_held_chats(member: User, db: Session) -> list[object]:
    """Hand back every conversation the departing member was holding.

    Without this the delete leaves a chat ``live`` with a null assignee, and
    ``OperatorHandler`` keeps swallowing the visitor's turns — no human is
    coming and the bot is muted, so the visitor types into nothing. The
    sweeper's idle release does eventually free it, but only after
    ``OPERATOR_RELEASE_IDLE_SECONDS`` (an hour by default), and nobody is told
    in the meantime.

    Reuses ``release_to_bot``, so a chat freed by a removal is indistinguish-
    able from one released by hand — and that also closes the dangling open
    ``operator_sessions`` stretch, which would otherwise sit open until the
    reconciliation pass found it. Staged, not committed: the releases belong
    to the same transaction as the delete. Returns the closed stretches for
    the caller to report after the commit.
    """
    from backend.chat.handlers.operator import release_to_bot

    held = (
        db.query(Chat)
        .filter(
            Chat.assigned_operator_id == member.id,
            Chat.operator_state == OperatorState.live,
        )
        .all()
    )
    closed = []
    for chat in held:
        stretch = release_to_bot(
            db, chat, reason=OperatorSessionEndReason.released
        )
        if stretch is not None:
            closed.append(stretch)
    return closed


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

    This is also how a seat is released. The seat lives on the row being
    deleted, so it goes with it — there is no separate revoke to remember, and
    no way to leave a workspace paying for a seat nobody holds.
    """
    _lock_workspace(tenant_id, db)
    member = get_member(tenant_id, member_id, db)
    if member.id == actor_id:
        raise HTTPException(
            status_code=400, detail="You cannot remove yourself from the workspace"
        )
    if member.role == ROLE_OWNER and count_owners(tenant_id, db) <= 1:
        raise HTTPException(
            status_code=400, detail="The last owner cannot be removed"
        )
    closed = _release_held_chats(member, db)
    _stamp_attribution(member, db)
    db.delete(member)
    db.commit()
    # After the commit, reading nothing: the removal has succeeded, and a
    # telemetry failure must not turn it into a 500 for the owner.
    for stretch in closed:
        emit_operator_session_ended(stretch)  # type: ignore[arg-type]
