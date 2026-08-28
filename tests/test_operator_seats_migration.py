"""Data steps of ``operator_seats_v1``, exercised directly.

The schema half (add a column, drop a column) is alembic's business and is
checked by running the migration. What is worth pinning here is the half that
touches rows, because both halves make the data agree with a model rather than
merely reshaping it, and both are irreversible:

* every member who had already joined must come out holding a seat, or they
  are stranded — the grant on acceptance only fires for a *pending* invitee,
  and the seat routes admit only an owner acting on themselves, so nothing
  could seat them afterwards;
* every workspace must come out with exactly one owner, or it is frozen —
  this build has no route that changes a role.

The functions take a bind, so they run against the test session's connection
with the ordinary ORM schema behind it.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.migrations.versions.operator_seats_v1 import (
    _one_owner_per_workspace,
    _seat_everyone_who_has_joined,
)
from backend.models import Tenant, User
from backend.models.base import _utcnow


def _tenant(db: Session, name: str) -> Tenant:
    row = Tenant(name=name, public_id=f"pub_{uuid.uuid4().hex[:12]}", settings={})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _user(
    db: Session,
    *,
    email: str,
    tenant: Tenant | None,
    role: str,
    verified: bool,
    age_days: int,
    seated: bool = False,
) -> User:
    row = User(
        email=email,
        password_hash="x",
        role=role,
        is_verified=verified,
        tenant_id=tenant.id if tenant else None,
        created_at=_utcnow() - timedelta(days=age_days),
        seat_granted_at=_utcnow() if seated else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_members_who_had_already_joined_are_seated(db_session: Session) -> None:
    """Otherwise every one of them 403s on /operator/* forever."""
    ws = _tenant(db_session, "Alpha")
    joined = _user(
        db_session,
        email="joined@alpha.example",
        tenant=ws,
        role="operator",
        verified=True,
        age_days=4,
    )

    _seat_everyone_who_has_joined(db_session.connection())
    db_session.expire_all()

    assert db_session.get(User, joined.id).seat_granted_at is not None


def test_the_backfill_leaves_owners_pending_invitees_and_detached_rows_alone(
    db_session: Session,
) -> None:
    """Three kinds of row that must not come out holding a seat.

    An owner buys their own; a pending invitee is seated when they accept and
    costs nothing until then; a detached row belongs to no workspace at all.
    """
    ws = _tenant(db_session, "Beta")
    owner = _user(
        db_session,
        email="owner@beta.example",
        tenant=ws,
        role="owner",
        verified=True,
        age_days=9,
    )
    pending = _user(
        db_session,
        email="pending@beta.example",
        tenant=ws,
        role="operator",
        verified=False,
        age_days=1,
    )
    detached = _user(
        db_session,
        email="detached@beta.example",
        tenant=None,
        role="operator",
        verified=True,
        age_days=3,
    )

    _seat_everyone_who_has_joined(db_session.connection())
    db_session.expire_all()

    assert db_session.get(User, owner.id).seat_granted_at is None
    assert db_session.get(User, pending.id).seat_granted_at is None
    assert db_session.get(User, detached.id).seat_granted_at is None


def test_the_backfill_does_not_move_a_seat_that_already_exists(
    db_session: Session,
) -> None:
    """Safe to run twice: it never re-stamps somebody already seated."""
    ws = _tenant(db_session, "Gamma")
    seated = _user(
        db_session,
        email="seated@gamma.example",
        tenant=ws,
        role="operator",
        verified=True,
        age_days=2,
        seated=True,
    )
    before = seated.seat_granted_at

    _seat_everyone_who_has_joined(db_session.connection())
    _seat_everyone_who_has_joined(db_session.connection())
    db_session.expire_all()

    assert db_session.get(User, seated.id).seat_granted_at == before


def test_a_crowded_workspace_keeps_its_founder_and_demotes_the_rest(
    db_session: Session,
) -> None:
    """The founder is its oldest member; everybody promoted came later."""
    ws = _tenant(db_session, "Delta")
    founder = _user(
        db_session,
        email="founder@delta.example",
        tenant=ws,
        role="owner",
        verified=True,
        age_days=10,
    )
    promoted = _user(
        db_session,
        email="promoted@delta.example",
        tenant=ws,
        role="owner",
        verified=True,
        age_days=5,
    )

    _one_owner_per_workspace(db_session.connection())
    db_session.expire_all()

    assert db_session.get(User, founder.id).role == "owner"
    assert db_session.get(User, promoted.id).role == "operator"


def test_a_pending_owner_invitation_is_demoted_before_it_can_be_accepted(
    db_session: Session,
) -> None:
    """The one path that could still mint a second owner after the deploy.

    The row was created under a build whose invitation could name a role; it
    is still sitting in ``users``, and accepting it afterwards would produce
    the owner this model says cannot exist.
    """
    ws = _tenant(db_session, "Epsilon")
    _user(
        db_session,
        email="founder@epsilon.example",
        tenant=ws,
        role="owner",
        verified=True,
        age_days=10,
    )
    invited_as_owner = _user(
        db_session,
        email="pending@epsilon.example",
        tenant=ws,
        role="owner",
        verified=False,
        age_days=1,
    )

    _one_owner_per_workspace(db_session.connection())
    db_session.expire_all()

    assert db_session.get(User, invited_as_owner.id).role == "operator"


def test_normalisation_never_leaves_a_workspace_without_an_owner(
    db_session: Session,
) -> None:
    """It keeps a row rather than counting one.

    Even where every owner is unverified — a workspace whose founder was
    demoted under the old build, leaving only invitations — one of them is
    kept rather than all of them demoted.
    """
    ws = _tenant(db_session, "Zeta")
    older = _user(
        db_session,
        email="older@zeta.example",
        tenant=ws,
        role="owner",
        verified=False,
        age_days=6,
    )
    newer = _user(
        db_session,
        email="newer@zeta.example",
        tenant=ws,
        role="owner",
        verified=False,
        age_days=2,
    )

    _one_owner_per_workspace(db_session.connection())
    db_session.expire_all()

    roles = sorted(
        [db_session.get(User, older.id).role, db_session.get(User, newer.id).role]
    )
    assert roles == ["operator", "owner"]
    assert db_session.get(User, older.id).role == "owner"


def test_normalisation_does_not_reach_across_workspaces(
    db_session: Session,
) -> None:
    """Each workspace keeps its own owner."""
    one = _tenant(db_session, "Eta")
    two = _tenant(db_session, "Theta")
    a = _user(
        db_session,
        email="a@eta.example",
        tenant=one,
        role="owner",
        verified=True,
        age_days=8,
    )
    b = _user(
        db_session,
        email="b@theta.example",
        tenant=two,
        role="owner",
        verified=True,
        age_days=3,
    )

    _one_owner_per_workspace(db_session.connection())
    db_session.expire_all()

    assert db_session.get(User, a.id).role == "owner"
    assert db_session.get(User, b.id).role == "owner"
