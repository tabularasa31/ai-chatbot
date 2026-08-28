"""Operator seats: who holds one, who may change that, and what it unlocks.

A seat is the per-person entitlement that replaced the workspace-level
``tenants.plan``. It is orthogonal to the role: the role says what you may
administer, the seat says whether you may operate. These tests pin the three
things that are easy to get wrong — the gate on ``/operator/*`` refusing a
seatless caller whatever their role, only an owner reaching the seat routes,
and the two helpers answering correctly when a seated member leaves.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth.service import create_token_for_user
from backend.models import Chat, OperatorState, User
from backend.models.base import _utcnow
from backend.seats.service import (
    count_seats,
    grant_seat,
    holds_seat,
    release_seat,
    tenant_has_any_seat,
    user_holds_seat,
)
from tests.conftest import register_and_verify_user

OTHER_PASSWORD = "OtherPass2@"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Workspace:
    def __init__(self, token: str, tenant_id: uuid.UUID, owner_id: uuid.UUID) -> None:
        self.token = token
        self.tenant_id = tenant_id
        self.owner_id = owner_id

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_workspace(
    client: TestClient, db: Session, *, email: str, name: str = "Seats Co"
) -> _Workspace:
    """A founding owner and their workspace. Not seated: signing up is not a
    purchase, so the owner starts on the ordinary e-mail path."""
    token = register_and_verify_user(client, db, email=email)
    resp = client.post("/tenants", headers=_auth(token), json={"name": name})
    assert resp.status_code in (200, 201), resp.text
    owner = db.query(User).filter(User.email == email).one()
    return _Workspace(token, uuid.UUID(resp.json()["id"]), owner.id)


def _onboard(
    client: TestClient,
    db: Session,
    ws: _Workspace,
    *,
    email: str,
    role: str = "operator",
) -> str:
    """Invite someone, accept for them, and return their JWT."""
    with patch("backend.tenants.members_service.send_email"):
        resp = client.post(
            "/tenants/members/invite",
            headers=ws.auth,
            json={"email": email, "role": role},
        )
    assert resp.status_code == 201, resp.text
    member = db.query(User).filter(User.email == email).one()
    accepted = client.post(
        "/auth/reset-password",
        json={"token": member.reset_password_token, "new_password": OTHER_PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    db.refresh(member)
    token, _ = create_token_for_user(member)
    return token


def _make_chat(db: Session, tenant_id: uuid.UUID) -> Chat:
    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        operator_state=OperatorState.bot,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _operator_calls(client: TestClient, chat_id: uuid.UUID, headers: dict[str, str]):
    """Every route a seat is sold for, as (name, response) pairs."""
    return [
        ("take", client.post(f"/operator/chats/{chat_id}/take", headers=headers)),
        (
            "messages",
            client.post(
                f"/operator/chats/{chat_id}/messages",
                headers=headers,
                json={"text": "Hello, this is a human."},
            ),
        ),
        ("release", client.post(f"/operator/chats/{chat_id}/release", headers=headers)),
    ]


# ---------------------------------------------------------------------------
# The gate on /operator/*
# ---------------------------------------------------------------------------


def test_seatless_owner_is_refused_on_every_operator_route(
    tenant: TestClient, db_session: Session
) -> None:
    """Administering everything is not the same as being allowed to answer."""
    ws = _make_workspace(tenant, db_session, email="seatless-owner@example.com")
    chat = _make_chat(db_session, ws.tenant_id)

    for name, resp in _operator_calls(tenant, chat.id, ws.auth):
        assert resp.status_code == 403, (name, resp.text)
        assert "seat" in resp.json()["detail"].lower(), name

    # And nothing happened to the conversation on the way out.
    db_session.expire_all()
    assert db_session.get(Chat, chat.id).operator_state is OperatorState.bot


def test_seatless_operator_is_refused_on_every_operator_route(
    tenant: TestClient, db_session: Session
) -> None:
    """The refusal is about the seat, not the role: both roles meet it."""
    ws = _make_workspace(tenant, db_session, email="owner-op@example.com")
    member_token = _onboard(tenant, db_session, ws, email="op@example.com")
    member = db_session.query(User).filter(User.email == "op@example.com").one()
    release_seat(member)
    db_session.commit()
    chat = _make_chat(db_session, ws.tenant_id)

    for name, resp in _operator_calls(tenant, chat.id, _auth(member_token)):
        assert resp.status_code == 403, (name, resp.text)
        assert "seat" in resp.json()["detail"].lower(), name


def test_seated_operator_reaches_every_operator_route(
    tenant: TestClient, db_session: Session
) -> None:
    """A seat admits the lower role, so the seat is the thing being sold."""
    ws = _make_workspace(tenant, db_session, email="owner-seated@example.com")
    member_token = _onboard(tenant, db_session, ws, email="seated-op@example.com")
    chat = _make_chat(db_session, ws.tenant_id)

    for name, resp in _operator_calls(tenant, chat.id, _auth(member_token)):
        assert resp.status_code == 200, (name, resp.text)


def test_owner_who_takes_a_seat_reaches_the_operator_routes(
    tenant: TestClient, db_session: Session
) -> None:
    """The refusal above is undone by the one act that is meant to undo it."""
    ws = _make_workspace(tenant, db_session, email="buying-owner@example.com")
    chat = _make_chat(db_session, ws.tenant_id)
    assert (
        tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth).status_code
        == 403
    )

    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    assert (
        tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth).status_code
        == 200
    )


def test_giving_up_a_seat_closes_the_operator_routes_again(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="returning-owner@example.com")
    chat = _make_chat(db_session, ws.tenant_id)
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    assert (
        tenant.post(f"/operator/chats/{chat.id}/release", headers=ws.auth).status_code
        == 200
    )

    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    resp = tenant.post(f"/operator/chats/{chat.id}/release", headers=ws.auth)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Who may change a seat
# ---------------------------------------------------------------------------


def test_giving_up_a_seat_hands_back_the_chats_you_were_holding(
    tenant: TestClient, db_session: Session
) -> None:
    """The seat you gave up is the seat the release button is behind.

    So the release has to happen on the way out, or the conversation is pinned
    ``live`` behind a 403 with the visitor typing into nothing.
    """
    from backend.models import OperatorSession

    ws = _make_workspace(tenant, db_session, email="holding@example.com")
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    chat = _make_chat(db_session, ws.tenant_id)
    taken = tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth)
    assert taken.status_code == 200, taken.text
    # The harness hands every request the one test session, so its identity map
    # still holds this chat as it was before the (async) take. A real request
    # gets a fresh session and reads the row as it stands; expiring here is what
    # makes the test see what production sees.
    db_session.expire_all()

    given_up = tenant.delete("/tenants/members/me/seat", headers=ws.auth)

    assert given_up.status_code == 200, given_up.text
    db_session.expire_all()
    refreshed = db_session.get(Chat, chat.id)
    assert refreshed.operator_state is OperatorState.bot
    assert refreshed.assigned_operator_id is None
    # And the stretch is closed, not left dangling for the reconciliation pass.
    stretches = (
        db_session.query(OperatorSession)
        .filter(OperatorSession.chat_id == chat.id)
        .all()
    )
    assert stretches, "the take should have opened a stretch"
    assert all(st.ended_at is not None for st in stretches)


def test_giving_up_a_seat_leaves_a_colleagues_chat_alone(
    tenant: TestClient, db_session: Session
) -> None:
    """Only your own conversations are handed back."""
    ws = _make_workspace(tenant, db_session, email="mine-only@example.com")
    colleague_token = _onboard(tenant, db_session, ws, email="colleague@example.com")
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    theirs = _make_chat(db_session, ws.tenant_id)
    assert (
        tenant.post(
            f"/operator/chats/{theirs.id}/take", headers=_auth(colleague_token)
        ).status_code
        == 200
    )
    db_session.expire_all()  # see the note in the test above

    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    db_session.expire_all()
    assert db_session.get(Chat, theirs.id).operator_state is OperatorState.live


def test_an_operator_cannot_reach_the_seat_routes(
    tenant: TestClient, db_session: Session
) -> None:
    """Seats are the owner's to hand out, and an operator holds one already."""
    ws = _make_workspace(tenant, db_session, email="owner-guard@example.com")
    member_token = _onboard(tenant, db_session, ws, email="guard-op@example.com")

    take = tenant.put("/tenants/members/me/seat", headers=_auth(member_token))
    give_back = tenant.delete("/tenants/members/me/seat", headers=_auth(member_token))

    assert take.status_code == 403, take.text
    assert give_back.status_code == 403, give_back.text
    # The refusal did not quietly take their seat away either.
    member = db_session.query(User).filter(User.email == "guard-op@example.com").one()
    db_session.refresh(member)
    assert member.seat_granted_at is not None


def test_the_seat_routes_need_authentication(tenant: TestClient) -> None:
    assert tenant.put("/tenants/members/me/seat").status_code in (401, 403)
    assert tenant.delete("/tenants/members/me/seat").status_code in (401, 403)


def test_taking_a_seat_twice_keeps_the_date_it_was_taken(
    tenant: TestClient, db_session: Session
) -> None:
    """Idempotent: a repeated grant is the same seat, not a newer one."""
    ws = _make_workspace(tenant, db_session, email="idem@example.com")

    first = tenant.put("/tenants/members/me/seat", headers=ws.auth)
    second = tenant.put("/tenants/members/me/seat", headers=ws.auth)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["seat_granted_at"] == second.json()["seat_granted_at"]


def test_giving_up_a_seat_twice_is_a_no_op(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="idem-release@example.com")
    tenant.put("/tenants/members/me/seat", headers=ws.auth)

    first = tenant.delete("/tenants/members/me/seat", headers=ws.auth)
    second = tenant.delete("/tenants/members/me/seat", headers=ws.auth)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["seat_granted_at"] is None
    assert second.json()["seat_granted_at"] is None


# ---------------------------------------------------------------------------
# Where seats come from and go
# ---------------------------------------------------------------------------


def test_a_founding_owner_starts_with_no_seat(
    tenant: TestClient, db_session: Session
) -> None:
    """Signing up is not a purchase."""
    ws = _make_workspace(tenant, db_session, email="fresh-owner@example.com")

    listing = tenant.get("/tenants/members", headers=ws.auth)

    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["seats"] == 0
    assert body["items"][0]["seat_granted_at"] is None


def test_a_pending_invitee_holds_no_seat_and_is_not_counted(
    tenant: TestClient, db_session: Session
) -> None:
    """The seat starts at acceptance, not at the invitation.

    A pending invitee is a placeholder for a person who may never turn up, so
    nothing is counted for them and a typo'd address costs nothing at all.
    Fails if ``grant_seat`` is ever put back into the invite path.
    """
    ws = _make_workspace(tenant, db_session, email="inviter@example.com")

    with patch("backend.tenants.members_service.send_email"):
        resp = tenant.post(
            "/tenants/members/invite",
            headers=ws.auth,
            json={"email": "invited@example.com", "role": "operator"},
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["member"]["status"] == "pending"
    assert resp.json()["member"]["seat_granted_at"] is None
    invitee = db_session.query(User).filter(User.email == "invited@example.com").one()
    assert invitee.seat_granted_at is None

    listing = tenant.get("/tenants/members", headers=ws.auth).json()
    assert listing["seats"] == 0
    assert count_seats(tenant_id=ws.tenant_id, db=db_session) == 0
    # The only member row in this workspace is a pending one, so the
    # workspace-level helper must say no as well.
    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is False


def test_accepting_the_invitation_grants_the_seat(
    tenant: TestClient, db_session: Session
) -> None:
    """Joining is what is paid for, and accepting is what joining means."""
    ws = _make_workspace(tenant, db_session, email="accept-owner@example.com")
    with patch("backend.tenants.members_service.send_email"):
        invited = tenant.post(
            "/tenants/members/invite",
            headers=ws.auth,
            json={"email": "accepts@example.com", "role": "operator"},
        )
    assert invited.status_code == 201, invited.text
    member = db_session.query(User).filter(User.email == "accepts@example.com").one()

    accepted = tenant.post(
        "/auth/reset-password",
        json={"token": member.reset_password_token, "new_password": OTHER_PASSWORD},
    )

    assert accepted.status_code == 200, accepted.text
    db_session.refresh(member)
    assert member.seat_granted_at is not None
    assert user_holds_seat(user_id=member.id, db=db_session) is True
    assert tenant.get("/tenants/members", headers=ws.auth).json()["seats"] == 1


def test_an_invitation_that_expires_unaccepted_leaves_the_count_untouched(
    tenant: TestClient, db_session: Session
) -> None:
    """The purge has nothing to release, because nothing was ever granted."""
    from backend.jobs.expired_invitations_purge import purge_expired_invitations

    ws = _make_workspace(tenant, db_session, email="expiry-owner@example.com")
    with patch("backend.tenants.members_service.send_email"):
        tenant.post(
            "/tenants/members/invite",
            headers=ws.auth,
            json={"email": "never-arrives@example.com", "role": "operator"},
        )
    stale = db_session.query(User).filter(User.email == "never-arrives@example.com").one()
    stale.reset_password_expires_at = _utcnow() - timedelta(days=1)
    db_session.commit()
    assert count_seats(tenant_id=ws.tenant_id, db=db_session) == 0

    assert purge_expired_invitations(db_session) == 1

    db_session.expire_all()
    assert count_seats(tenant_id=ws.tenant_id, db=db_session) == 0
    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is False
    assert tenant.get("/tenants/members", headers=ws.auth).json()["seats"] == 0


def test_a_password_reset_never_hands_back_a_seat_the_owner_gave_up(
    tenant: TestClient, db_session: Session
) -> None:
    """Accepting and resetting share a token; only one of them seats anybody.

    An owner who deliberately gave their seat back must not have it returned by
    their next forgotten password — which is why the grant is conditioned on
    the row having been a pending invitee rather than on the endpoint.
    """
    ws = _make_workspace(tenant, db_session, email="resetter@example.com")
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    from backend.auth.service import create_reset_token

    token = create_reset_token("resetter@example.com", db_session)
    reset = tenant.post(
        "/auth/reset-password",
        json={"token": token, "new_password": OTHER_PASSWORD},
    )

    assert reset.status_code == 200, reset.text
    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    db_session.refresh(owner)
    assert owner.seat_granted_at is None
    assert count_seats(tenant_id=ws.tenant_id, db=db_session) == 0


def test_removing_a_member_releases_their_seat(
    tenant: TestClient, db_session: Session
) -> None:
    """The seat lives on the deleted row, so there is no seat left behind."""
    ws = _make_workspace(tenant, db_session, email="remover@example.com")
    _onboard(tenant, db_session, ws, email="leaving@example.com")
    member = db_session.query(User).filter(User.email == "leaving@example.com").one()
    assert tenant.get("/tenants/members", headers=ws.auth).json()["seats"] == 1

    removed = tenant.delete(f"/tenants/members/{member.id}", headers=ws.auth)

    assert removed.status_code == 204, removed.text
    assert tenant.get("/tenants/members", headers=ws.auth).json()["seats"] == 0
    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is False


def test_changing_a_role_leaves_the_seat_alone(
    tenant: TestClient, db_session: Session
) -> None:
    """The two axes do not move each other."""
    ws = _make_workspace(tenant, db_session, email="promoter@example.com")
    _onboard(tenant, db_session, ws, email="promoted@example.com")
    member = db_session.query(User).filter(User.email == "promoted@example.com").one()
    before = member.seat_granted_at

    resp = tenant.patch(
        f"/tenants/members/{member.id}", headers=ws.auth, json={"role": "owner"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "owner"
    db_session.refresh(member)
    assert member.seat_granted_at == before


# ---------------------------------------------------------------------------
# The two helpers
# ---------------------------------------------------------------------------


def test_tenant_has_any_seat_across_the_three_states(
    tenant: TestClient, db_session: Session
) -> None:
    """No seats at all, somebody seated, and that somebody gone again."""
    ws = _make_workspace(tenant, db_session, email="helper-owner@example.com")

    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is False

    _onboard(tenant, db_session, ws, email="helper-op@example.com")
    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is True

    member = db_session.query(User).filter(User.email == "helper-op@example.com").one()
    assert tenant.delete(
        f"/tenants/members/{member.id}", headers=ws.auth
    ).status_code == 204
    db_session.expire_all()
    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is False


def test_tenant_has_any_seat_ignores_another_workspaces_seats(
    tenant: TestClient, db_session: Session
) -> None:
    """The question is about one workspace, not about seats existing."""
    mine = _make_workspace(tenant, db_session, email="mine@example.com", name="Mine")
    theirs = _make_workspace(
        tenant, db_session, email="theirs@example.com", name="Theirs"
    )
    _onboard(tenant, db_session, theirs, email="their-op@example.com")

    assert tenant_has_any_seat(tenant_id=theirs.tenant_id, db=db_session) is True
    assert tenant_has_any_seat(tenant_id=mine.tenant_id, db=db_session) is False


def test_tenant_has_any_seat_drops_a_member_detached_from_the_workspace(
    tenant: TestClient, db_session: Session
) -> None:
    """``users.tenant_id`` is nullable — a detached row holds no seat here.

    Reached by the FK's ``ON DELETE SET NULL`` when a workspace goes away, so
    the row can outlive its membership while the column still holds a date.
    """
    ws = _make_workspace(tenant, db_session, email="detach-owner@example.com")
    _onboard(tenant, db_session, ws, email="detached@example.com")
    member = db_session.query(User).filter(User.email == "detached@example.com").one()

    member.tenant_id = None
    db_session.commit()

    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is False
    assert user_holds_seat(user_id=member.id, db=db_session) is False


def test_user_holds_seat_answers_per_person(
    tenant: TestClient, db_session: Session
) -> None:
    """Two people in one seated workspace, and only one of them is seated."""
    ws = _make_workspace(tenant, db_session, email="per-person@example.com")
    _onboard(tenant, db_session, ws, email="seated-one@example.com")
    seated = db_session.query(User).filter(User.email == "seated-one@example.com").one()

    assert tenant_has_any_seat(tenant_id=ws.tenant_id, db=db_session) is True
    assert user_holds_seat(user_id=seated.id, db=db_session) is True
    # The owner is in the same workspace and holds nothing.
    assert user_holds_seat(user_id=ws.owner_id, db=db_session) is False


def test_user_holds_seat_is_false_for_an_account_that_is_gone(
    tenant: TestClient, db_session: Session
) -> None:
    """A removed member is exactly somebody who no longer holds a seat."""
    ws = _make_workspace(tenant, db_session, email="gone-owner@example.com")
    _onboard(tenant, db_session, ws, email="gone@example.com")
    member = db_session.query(User).filter(User.email == "gone@example.com").one()
    member_id = member.id

    assert tenant.delete(
        f"/tenants/members/{member_id}", headers=ws.auth
    ).status_code == 204

    assert user_holds_seat(user_id=member_id, db=db_session) is False
    assert user_holds_seat(user_id=uuid.uuid4(), db=db_session) is False


def test_holds_seat_predicate_needs_both_a_workspace_and_a_grant() -> None:
    """The predicate the auth dependency uses, over rows it never queries."""
    assert holds_seat(None) is False
    assert (
        holds_seat(User(tenant_id=uuid.uuid4(), seat_granted_at=None)) is False
    )
    assert holds_seat(User(tenant_id=None, seat_granted_at=_utcnow())) is False
    assert holds_seat(User(tenant_id=uuid.uuid4(), seat_granted_at=_utcnow())) is True


def test_count_seats_counts_this_workspace_only(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="counter@example.com")
    _onboard(tenant, db_session, ws, email="count-a@example.com")
    _onboard(tenant, db_session, ws, email="count-b@example.com")

    assert count_seats(tenant_id=ws.tenant_id, db=db_session) == 2

    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    grant_seat(owner)
    db_session.commit()
    assert count_seats(tenant_id=ws.tenant_id, db=db_session) == 3


# ---------------------------------------------------------------------------
# What the plan field left behind
# ---------------------------------------------------------------------------


def test_the_plan_endpoints_are_gone(tenant: TestClient, db_session: Session) -> None:
    """The workspace-level tier is not merely unused — it is unreachable."""
    ws = _make_workspace(tenant, db_session, email="no-plan@example.com")

    assert tenant.get("/tenants/me/plan", headers=ws.auth).status_code == 404
    assert (
        tenant.put(
            "/tenants/me/plan", headers=ws.auth, json={"plan": "pro"}
        ).status_code
        == 404
    )
