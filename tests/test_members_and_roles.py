"""Roles and team members, phase 0.5.

Covers the four things the feature stands on — an invited colleague really
lands in the same workspace, an operator reaches the work surfaces and is
refused the owner ones, a workspace can never be left without an owner, and an
invite link is single-use and mortal — plus tenant isolation on every member
route and the tenant-less principal that ``users.tenant_id`` being nullable
makes possible.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth.service import create_token_for_user
from backend.models import (
    Chat,
    GapDismissal,
    Message,
    MessageRole,
    OperatorSession,
    OperatorState,
    Tenant,
    TenantApiKey,
    User,
)
from backend.models.base import _utcnow
from tests.conftest import register_and_verify_user

PASSWORD = "SecurePass1!"
OTHER_PASSWORD = "OtherPass2@"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _Workspace:
    def __init__(self, token: str, tenant_id: uuid.UUID, owner_id: uuid.UUID) -> None:
        self.token = token
        self.tenant_id = tenant_id
        self.owner_id = owner_id

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _make_workspace(
    client: TestClient, db: Session, *, email: str, name: str = "Acme"
) -> _Workspace:
    token = register_and_verify_user(client, db, email=email)
    resp = client.post(
        "/tenants", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert resp.status_code in (200, 201), resp.text
    owner = db.query(User).filter(User.email == email).one()
    return _Workspace(token, uuid.UUID(resp.json()["id"]), owner.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _invite(
    client: TestClient, workspace: _Workspace, *, email: str, role: str = "operator"
):
    with patch("backend.tenants.members_service.send_email") as sent:
        resp = client.post(
            "/tenants/members/invite",
            headers=workspace.auth,
            json={"email": email, "role": role},
        )
    return resp, sent


def _invite_token(db: Session, email: str) -> str:
    member = db.query(User).filter(User.email == email).one()
    assert member.reset_password_token is not None
    return member.reset_password_token


def _accept(client: TestClient, token: str, password: str = OTHER_PASSWORD):
    return client.post(
        "/auth/reset-password", json={"token": token, "new_password": password}
    )


def _member_token(db: Session, email: str) -> str:
    member = db.query(User).filter(User.email == email).one()
    jwt_token, _ = create_token_for_user(member)
    return jwt_token


def _onboard_operator(
    client: TestClient, db: Session, workspace: _Workspace, *, email: str
) -> str:
    """Invite someone, accept the invite, and return their JWT."""
    resp, _sent = _invite(client, workspace, email=email)
    assert resp.status_code == 201, resp.text
    assert _accept(client, _invite_token(db, email)).status_code == 200
    return _member_token(db, email)


# ---------------------------------------------------------------------------
# The invite round trip
# ---------------------------------------------------------------------------


def test_invited_colleague_sets_a_password_and_lands_in_the_same_tenant(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")

    resp, sent = _invite(tenant, ws, email="ops@acme.example.com")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["member"]["role"] == "operator"
    assert body["member"]["status"] == "pending"

    # The link the colleague receives points at the accept-invite page.
    assert sent.call_count == 1
    invite_body = sent.call_args.kwargs["body"]
    token = _invite_token(db_session, "ops@acme.example.com")
    assert f"/accept-invite?token={token}" in invite_body

    assert _accept(tenant, token).status_code == 200

    login = tenant.post(
        "/auth/login", json={"email": "ops@acme.example.com", "password": OTHER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    me = tenant.get("/tenants/me", headers=_auth(login.json()["token"]))
    assert me.status_code == 200, me.text
    assert me.json()["id"] == str(ws.tenant_id)
    assert me.json()["role"] == "operator"

    listing = tenant.get("/tenants/members", headers=ws.auth)
    assert listing.status_code == 200, listing.text
    rows = {row["email"]: row for row in listing.json()["items"]}
    assert rows["owner@acme.example.com"]["role"] == "owner"
    assert rows["ops@acme.example.com"]["status"] == "active"


def test_reinvite_reissues_the_token_and_may_change_the_role(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert _invite(tenant, ws, email="ops@acme.example.com")[0].status_code == 201
    first = _invite_token(db_session, "ops@acme.example.com")

    resp, _sent = _invite(tenant, ws, email="ops@acme.example.com", role="owner")
    assert resp.status_code == 201, resp.text
    assert resp.json()["member"]["role"] == "owner"

    db_session.expire_all()
    second = _invite_token(db_session, "ops@acme.example.com")
    assert second != first
    assert _accept(tenant, first).status_code == 400
    assert _accept(tenant, second).status_code == 200


def test_inviting_an_existing_member_or_a_stranger_conflicts(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    other = _make_workspace(
        tenant, db_session, email="owner@rival.example.com", name="Rival"
    )

    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    assert _invite(tenant, ws, email="ops@acme.example.com")[0].status_code == 409
    # Already runs their own workspace.
    assert _invite(tenant, ws, email="owner@rival.example.com")[0].status_code == 409
    assert other.tenant_id != ws.tenant_id


def test_invite_token_expires(tenant: TestClient, db_session: Session) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert _invite(tenant, ws, email="ops@acme.example.com")[0].status_code == 201

    member = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    token = member.reset_password_token
    member.reset_password_expires_at = _utcnow() - timedelta(minutes=1)
    db_session.commit()

    assert _accept(tenant, token).status_code == 400
    db_session.expire_all()
    member = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    assert member.is_verified is False


def test_invite_token_cannot_be_replayed(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert _invite(tenant, ws, email="ops@acme.example.com")[0].status_code == 201
    token = _invite_token(db_session, "ops@acme.example.com")

    assert _accept(tenant, token).status_code == 200
    # A second redemption of the same link must not reset the password again.
    assert _accept(tenant, token, password="Replayed3#").status_code == 400
    login = tenant.post(
        "/auth/login", json={"email": "ops@acme.example.com", "password": "Replayed3#"}
    )
    assert login.status_code == 401


# ---------------------------------------------------------------------------
# What each role may reach
# ---------------------------------------------------------------------------


def test_operator_reaches_the_inbox_the_logs_and_the_knowledge_reads(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    headers = _auth(op_token)

    chat = Chat(tenant_id=ws.tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()

    assert tenant.get("/chat/sessions", headers=headers).status_code == 200
    assert tenant.get("/escalations", headers=headers).status_code == 200
    assert tenant.get("/documents", headers=headers).status_code == 200
    assert tenant.get("/api/v1/knowledge/faq", headers=headers).status_code == 200
    assert tenant.get("/api/v1/knowledge/profile", headers=headers).status_code == 200
    assert tenant.get("/gap-analyzer", headers=headers).status_code == 200

    take = tenant.post(f"/operator/chats/{chat.id}/take", headers=headers)
    assert take.status_code == 200, take.text
    assert take.json()["operator_state"] == "live"
    assert (
        tenant.post(f"/operator/chats/{chat.id}/release", headers=headers).status_code
        == 200
    )


def test_operator_is_refused_settings_keys_privacy_and_member_management(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    headers = _auth(op_token)
    member_id = db_session.query(User).filter(User.email == "ops@acme.example.com").one().id

    refusals = [
        tenant.patch("/tenants/me", headers=headers, json={"name": "Renamed"}),
        tenant.get("/tenants/me/api-keys", headers=headers),
        tenant.post(
            "/tenants/me/api-keys/rotate",
            headers=headers,
            json={"reason": "other", "revoke_old_immediately": False},
        ),
        tenant.get("/tenants/me/privacy", headers=headers),
        tenant.put(
            "/tenants/me/privacy", headers=headers, json={"optional_entity_types": []}
        ),
        tenant.put(
            "/tenants/me/support-settings",
            headers=headers,
            json={"l2_email": "support@acme.example.com"},
        ),
        tenant.get("/tenants/members", headers=headers),
        tenant.post(
            "/tenants/members/invite",
            headers=headers,
            json={"email": "third@acme.example.com", "role": "operator"},
        ),
        tenant.patch(
            f"/tenants/members/{member_id}", headers=headers, json={"role": "owner"}
        ),
        tenant.delete(f"/tenants/members/{ws.owner_id}", headers=headers),
    ]
    assert [r.status_code for r in refusals] == [403] * len(refusals)

    # Readable, though: these are the support contacts the bot hands to
    # visitors, and the operator working the inbox is who gets asked.
    assert (
        tenant.get("/tenants/me/support-settings", headers=headers).status_code == 200
    )

    # The workspace is untouched by the attempt.
    me = tenant.get("/tenants/me", headers=ws.auth)
    assert me.json()["name"] == "Acme"


def test_operator_cannot_edit_the_knowledge_base(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    headers = _auth(op_token)

    assert (
        tenant.post(
            "/documents/sources/url",
            headers=headers,
            json={"url": "https://acme.example.com/docs"},
        ).status_code
        == 403
    )
    assert (
        tenant.delete(f"/documents/{uuid.uuid4()}", headers=headers).status_code == 403
    )
    assert (
        tenant.patch(
            "/api/v1/knowledge/profile", headers=headers, json={"topics": ["billing"]}
        ).status_code
        == 403
    )
    assert (
        tenant.post("/api/v1/knowledge/faq/approve-all", headers=headers).status_code == 403
    )


def test_only_the_owner_may_publish_an_faq(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    gap_id = uuid.uuid4()

    assert (
        tenant.post(
            f"/gap-analyzer/mode_b/{gap_id}/publish", headers=_auth(op_token)
        ).status_code
        == 403
    )
    # The owner is let through the role gate: what stops them is the missing
    # draft, not the role.
    owner_attempt = tenant.post(
        f"/gap-analyzer/mode_b/{gap_id}/publish", headers=ws.auth
    )
    assert owner_attempt.status_code != 403, owner_attempt.text

    # Preparing a draft is not publishing — an operator may still do that.
    assert (
        tenant.get(
            f"/gap-analyzer/mode_b/{gap_id}/draft", headers=_auth(op_token)
        ).status_code
        != 403
    )


# ---------------------------------------------------------------------------
# The last-owner guard
# ---------------------------------------------------------------------------


def test_the_last_owner_cannot_be_removed(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    # Promote, so the operator can try to remove the founder without the
    # self-removal rule being what refuses it.
    successor = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    assert (
        tenant.patch(
            f"/tenants/members/{successor.id}", headers=ws.auth, json={"role": "owner"}
        ).status_code
        == 200
    )
    # The founder takes a seat first: an operator without one could not answer,
    # so a seatless owner cannot be demoted. See tests/test_seats.py.
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    # Demote the founder (legally — a second owner exists), leaving one owner.
    assert (
        tenant.patch(
            f"/tenants/members/{ws.owner_id}",
            headers=_auth(op_token),
            json={"role": "operator"},
        ).status_code
        == 200
    )

    # Now the successor is the last owner, and the founder cannot remove them.
    removed = tenant.delete(
        f"/tenants/members/{successor.id}", headers=_auth(op_token)
    )
    assert removed.status_code == 400, removed.text
    assert "yourself" in removed.json()["detail"].lower()
    db_session.expire_all()
    assert db_session.query(User).filter(User.id == successor.id).one().role == "owner"


def test_nobody_can_demote_themselves(
    tenant: TestClient, db_session: Session
) -> None:
    """The lockout that used to be reachable, from both directions.

    A sole owner demoting themselves is the unrecoverable move: the surface
    that could put the role back is the one they just left. It is refused
    whether or not the workspace has anyone else, and — the regression that
    made this urgent — whether or not an unaccepted invitation makes the owner
    count *look* like two.
    """
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")

    # Alone.
    solo = tenant.patch(
        f"/tenants/members/{ws.owner_id}", headers=ws.auth, json={"role": "operator"}
    )
    assert solo.status_code == 400, solo.text
    assert "yourself" in solo.json()["detail"].lower()

    # With a pending owner invitation outstanding — the row exists, has the
    # owner role, and must not count for anything.
    resp, _sent = _invite(tenant, ws, email="typo@exmaple.example.com", role="owner")
    assert resp.status_code == 201, resp.text
    with_pending = tenant.patch(
        f"/tenants/members/{ws.owner_id}", headers=ws.auth, json={"role": "operator"}
    )
    assert with_pending.status_code == 400, with_pending.text

    db_session.expire_all()
    assert db_session.query(User).filter(User.id == ws.owner_id).one().role == "owner"
    # And the owner surfaces still answer to them.
    assert tenant.get("/tenants/members", headers=ws.auth).status_code == 200


def test_a_pending_invitation_does_not_count_as_an_owner(
    tenant: TestClient, db_session: Session
) -> None:
    """``count_owners`` sees people, not invitations."""
    from backend.tenants.members_service import count_owners

    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert count_owners(ws.tenant_id, db_session) == 1
    assert (
        _invite(tenant, ws, email="pending@acme.example.com", role="owner")[
            0
        ].status_code
        == 201
    )
    db_session.expire_all()
    assert count_owners(ws.tenant_id, db_session) == 1

    # It becomes 2 only once the invitation is actually accepted.
    assert _accept(tenant, _invite_token(db_session, "pending@acme.example.com")).status_code == 200
    db_session.expire_all()
    assert count_owners(ws.tenant_id, db_session) == 2


def test_the_last_owner_cannot_be_demoted_by_another_actor(
    tenant: TestClient, db_session: Session
) -> None:
    """Defence in depth, exercised at the service layer.

    Through the API this branch is unreachable: the actor must be an owner of
    the workspace, so a target who is its *only* owner is always the actor
    themselves, and the self-demotion rule refuses first. The guard stays for
    any future caller that is not the member being changed.
    """
    from backend.tenants.members_service import change_member_role

    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    with pytest.raises(HTTPException) as exc:
        change_member_role(
            tenant_id=ws.tenant_id,
            actor_id=uuid.uuid4(),
            member_id=ws.owner_id,
            role="operator",
            db=db_session,
        )
    assert exc.value.status_code == 400
    assert "last owner" in exc.value.detail.lower()


def test_nobody_can_remove_themselves(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    # Two owners, so the last-owner guard is not what refuses this.
    second = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    assert (
        tenant.patch(
            f"/tenants/members/{second.id}", headers=ws.auth, json={"role": "owner"}
        ).status_code
        == 200
    )

    resp = tenant.delete(f"/tenants/members/{ws.owner_id}", headers=ws.auth)
    assert resp.status_code == 400, resp.text
    assert "yourself" in resp.json()["detail"].lower()

    db_session.expire_all()
    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    assert owner.tenant_id == ws.tenant_id


def test_succession_promote_then_demote_the_original_owner(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    successor = db_session.query(User).filter(User.email == "ops@acme.example.com").one()

    assert (
        tenant.patch(
            f"/tenants/members/{successor.id}", headers=ws.auth, json={"role": "owner"}
        ).status_code
        == 200
    )
    # The founder takes a seat first: stepping down to operator means taking on
    # the job an operator does, and that needs a seat. See tests/test_seats.py.
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    # The successor demotes the founder. Nobody demotes themselves, so handing
    # over is always a two-party act.
    assert (
        tenant.patch(
            f"/tenants/members/{ws.owner_id}",
            headers=_auth(op_token),
            json={"role": "operator"},
        ).status_code
        == 200
    )
    # And the demoted founder is an operator from the next request on.
    assert tenant.get("/tenants/members", headers=ws.auth).status_code == 403
    assert tenant.get("/tenants/members", headers=_auth(op_token)).status_code == 200


def test_removing_a_member_deletes_the_account_and_kills_their_session(
    tenant: TestClient, db_session: Session
) -> None:
    """Membership and account have the same lifetime.

    A live JWT in the departing member's browser must stop working at once —
    the token is stateless, so what invalidates it is the user row being gone.
    """
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    member = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    chat = Chat(tenant_id=ws.tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()

    assert (
        tenant.delete(
            f"/tenants/members/{member.id}", headers=ws.auth
        ).status_code
        == 204
    )

    db_session.expire_all()
    assert (
        db_session.query(User).filter(User.email == "ops@acme.example.com").first()
        is None
    )

    # 401, not 404: there is no longer a principal, never mind a workspace.
    headers = _auth(op_token)
    assert tenant.get("/tenants/me", headers=headers).status_code == 401
    assert tenant.get("/tenants/members", headers=headers).status_code == 401
    assert (
        tenant.post(f"/operator/chats/{chat.id}/take", headers=headers).status_code
        == 401
    )
    # And they cannot log back in with the password they set.
    assert (
        tenant.post(
            "/auth/login",
            json={"email": "ops@acme.example.com", "password": OTHER_PASSWORD},
        ).status_code
        == 401
    )

    listing = tenant.get("/tenants/members", headers=ws.auth).json()["items"]
    assert [row["email"] for row in listing] == ["owner@acme.example.com"]


def test_removal_keeps_the_signature_on_the_history(
    tenant: TestClient, db_session: Session
) -> None:
    """The account goes; who did the work stays.

    ``SET NULL`` on every FK into ``users`` would erase authorship silently,
    and nothing reads these fields yet (the console is phase 2), so the loss
    would surface only when someone asked who handled a ticket.
    """
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    member = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    member_id = member.id

    chat = Chat(tenant_id=ws.tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    reply = Message(
        chat_id=chat.id,
        role=MessageRole.operator,
        content="Refunds take 14 days.",
        operator_user_id=member_id,
    )
    stretch = OperatorSession(
        tenant_id=ws.tenant_id,
        chat_id=chat.id,
        operator_user_id=member_id,
        joined_at=_utcnow(),
    )
    dismissal = GapDismissal(
        tenant_id=ws.tenant_id,
        source="mode_a",
        gap_id=uuid.uuid4(),
        reason="not_relevant",
        dismissed_by=member_id,
    )
    db_session.add_all([reply, stretch, dismissal])
    db_session.commit()

    assert (
        tenant.delete(
            f"/tenants/members/{member_id}", headers=ws.auth
        ).status_code
        == 204
    )

    db_session.expire_all()
    reply = db_session.query(Message).filter(Message.id == reply.id).one()
    stretch = db_session.query(OperatorSession).filter(
        OperatorSession.id == stretch.id
    ).one()
    dismissal = db_session.query(GapDismissal).filter(
        GapDismissal.id == dismissal.id
    ).one()

    # The id is gone with the account, the signature is not.
    assert reply.operator_user_id is None
    assert reply.operator_label == "ops@acme.example.com"
    assert reply.content == "Refunds take 14 days."
    assert stretch.operator_user_id is None
    assert stretch.operator_label == "ops@acme.example.com"
    # SET NULL rather than CASCADE: a dismissed gap must not come back just
    # because the person who dismissed it left.
    assert dismissal.dismissed_by is None
    assert dismissal.dismissed_by_label == "ops@acme.example.com"


def test_a_reinvited_address_gets_a_new_account_and_old_history_keeps_the_label(
    tenant: TestClient, db_session: Session
) -> None:
    """Coming back is a new account, and nothing has to reconcile the two."""
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    first = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    first_id = first.id
    chat = Chat(tenant_id=ws.tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    reply = Message(
        chat_id=chat.id,
        role=MessageRole.operator,
        content="Earlier answer.",
        operator_user_id=first_id,
    )
    db_session.add(reply)
    db_session.commit()

    assert (
        tenant.delete(f"/tenants/members/{first_id}", headers=ws.auth).status_code
        == 204
    )
    # Invited again: a fresh account, and a fresh set-password link.
    second_token = _onboard_operator(
        tenant, db_session, ws, email="ops@acme.example.com"
    )
    second = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    assert second.id != first_id

    db_session.expire_all()
    reply = db_session.query(Message).filter(Message.id == reply.id).one()
    # Old work stays attributed by label, not re-attributed to the new account.
    assert reply.operator_user_id is None
    assert reply.operator_label == "ops@acme.example.com"

    assert tenant.get("/tenants/me", headers=_auth(second_token)).status_code == 200


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_member_routes_are_tenant_scoped(
    tenant: TestClient, db_session: Session
) -> None:
    ours = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    theirs = _make_workspace(
        tenant, db_session, email="owner@rival.example.com", name="Rival"
    )
    _onboard_operator(tenant, db_session, theirs, email="ops@rival.example.com")
    stranger = db_session.query(User).filter(User.email == "ops@rival.example.com").one()

    # A member id from another workspace is indistinguishable from a made-up one.
    assert (
        tenant.patch(
            f"/tenants/members/{stranger.id}", headers=ours.auth, json={"role": "owner"}
        ).status_code
        == 404
    )
    assert (
        tenant.delete(
            f"/tenants/members/{stranger.id}", headers=ours.auth
        ).status_code
        == 404
    )
    assert (
        tenant.delete(
            f"/tenants/members/{uuid.uuid4()}", headers=ours.auth
        ).status_code
        == 404
    )

    listing = tenant.get("/tenants/members", headers=ours.auth)
    assert [row["email"] for row in listing.json()["items"]] == ["owner@acme.example.com"]


def test_cross_tenant_chats_stay_404_for_an_operator(
    tenant: TestClient, db_session: Session
) -> None:
    ours = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    theirs = _make_workspace(
        tenant, db_session, email="owner@rival.example.com", name="Rival"
    )
    op_token = _onboard_operator(tenant, db_session, ours, email="ops@acme.example.com")
    foreign = Chat(tenant_id=theirs.tenant_id, session_id=uuid.uuid4())
    db_session.add(foreign)
    db_session.commit()

    headers = _auth(op_token)
    for resp in (
        tenant.post(f"/operator/chats/{foreign.id}/take", headers=headers),
        tenant.post(
            f"/operator/chats/{foreign.id}/messages",
            headers=headers,
            json={"text": "hello"},
        ),
        tenant.post(f"/operator/chats/{foreign.id}/release", headers=headers),
    ):
        assert resp.status_code == 404, resp.text


def test_a_principal_without_a_workspace_is_refused(
    tenant: TestClient, db_session: Session
) -> None:
    """``users.tenant_id`` is nullable, so the role check must handle NULL.

    Not a state removal produces any more — removal deletes the account — but
    the column is still nullable and the window between registering and
    verifying still exists, so the dependency has to answer for it. Their
    ``role`` column reads "owner" here, its default, which is exactly why the
    check must look at the membership before the role: reading the role alone
    would promote a principal who belongs to nothing.
    """
    token = register_and_verify_user(tenant, db_session, email="nobody@acme.example.com")
    orphan = db_session.query(User).filter(User.email == "nobody@acme.example.com").one()
    assert orphan.tenant_id is None
    assert orphan.role == "owner"

    headers = _auth(token)
    assert tenant.get("/tenants/members", headers=headers).status_code == 404
    assert (
        tenant.post(
            "/tenants/members/invite",
            headers=headers,
            json={"email": "someone@acme.example.com", "role": "operator"},
        ).status_code
        == 404
    )
    assert (
        tenant.post(
            f"/operator/chats/{uuid.uuid4()}/take", headers=headers
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# What a removal has to clean up behind it
# ---------------------------------------------------------------------------


def test_removal_hands_back_the_chats_the_member_was_holding(
    tenant: TestClient, db_session: Session
) -> None:
    """A live chat with no operator is a visitor typing into nothing.

    ``OperatorHandler`` swallows every visitor turn while a chat is ``live``,
    so a chat left held by a deleted account answers with neither a human nor
    the bot until the sweeper's idle release fires — up to an hour later, with
    nobody told.
    """
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    member = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    member_id = member.id

    held = Chat(tenant_id=ws.tenant_id, session_id=uuid.uuid4())
    untouched = Chat(tenant_id=ws.tenant_id, session_id=uuid.uuid4())
    db_session.add_all([held, untouched])
    db_session.commit()
    db_session.refresh(held)
    db_session.refresh(untouched)

    assert (
        tenant.post(f"/operator/chats/{held.id}/take", headers=_auth(op_token)).status_code
        == 200
    )
    db_session.expire_all()
    assert db_session.query(Chat).filter(Chat.id == held.id).one().operator_state is (
        OperatorState.live
    )
    stretch = (
        db_session.query(OperatorSession)
        .filter(OperatorSession.chat_id == held.id)
        .one()
    )
    assert stretch.ended_at is None

    assert (
        tenant.delete(f"/tenants/members/{member_id}", headers=ws.auth).status_code
        == 204
    )

    db_session.expire_all()
    freed = db_session.query(Chat).filter(Chat.id == held.id).one()
    assert freed.operator_state is OperatorState.bot
    assert freed.assigned_operator_id is None
    assert freed.operator_released_at is not None
    # The open stretch is closed too, not left dangling for the reconciliation
    # pass to find.
    stretch = (
        db_session.query(OperatorSession)
        .filter(OperatorSession.chat_id == held.id)
        .one()
    )
    assert stretch.ended_at is not None
    assert stretch.operator_label == "ops@acme.example.com"
    # A chat they never held is not touched.
    assert (
        db_session.query(Chat).filter(Chat.id == untouched.id).one().operator_state
        is OperatorState.bot
    )


def test_removal_signs_the_api_keys_the_member_issued(
    tenant: TestClient, db_session: Session
) -> None:
    """Who issued a widget key is the first question when one leaks."""
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    op_token = _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    successor = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    assert (
        tenant.patch(
            f"/tenants/members/{successor.id}", headers=ws.auth, json={"role": "owner"}
        ).status_code
        == 200
    )
    rotated = tenant.post(
        "/tenants/me/api-keys/rotate",
        headers=_auth(op_token),
        json={"reason": "scheduled", "revoke_old_immediately": False},
    )
    assert rotated.status_code == 201, rotated.text
    key_id = uuid.UUID(rotated.json()["key"]["id"])
    assert (
        db_session.query(TenantApiKey).filter(TenantApiKey.id == key_id).one()
        .created_by_user_id
        == successor.id
    )

    assert (
        tenant.delete(f"/tenants/members/{successor.id}", headers=ws.auth).status_code
        == 204
    )
    db_session.expire_all()
    key = db_session.query(TenantApiKey).filter(TenantApiKey.id == key_id).one()
    assert key.created_by_user_id is None
    assert key.created_by_label == "ops@acme.example.com"


def test_deleting_a_workspace_deletes_its_members(
    tenant: TestClient, db_session: Session
) -> None:
    """Otherwise it produces the orphan removal exists to avoid — and burns
    the address, since nothing but this code could ever free it."""
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")
    assert (
        tenant.delete(f"/tenants/{ws.tenant_id}", headers=ws.auth).status_code == 204
    )

    db_session.expire_all()
    assert db_session.query(Tenant).filter(Tenant.id == ws.tenant_id).first() is None
    for email in ("owner@acme.example.com", "ops@acme.example.com"):
        assert db_session.query(User).filter(User.email == email).first() is None

    # The addresses are free again: they can sign up from scratch.
    with patch("backend.auth.routes.send_email"):
        again = tenant.post(
            "/auth/register",
            json={"email": "ops@acme.example.com", "password": PASSWORD},
        )
    assert again.status_code == 200, again.text


# ---------------------------------------------------------------------------
# The invite token and the reset token share a column
# ---------------------------------------------------------------------------


def test_forgot_password_resends_the_invite_instead_of_voiding_it(
    tenant: TestClient, db_session: Session
) -> None:
    """The two acts are the same wish — let me in — so they must not fight.

    A pending invitee cannot log in, so "Forgot password" is exactly what they
    press. Issuing a reset would overwrite the invite token and cut its life
    to an hour, after which the invite link they eventually find reports
    "invalid or expired" and the owner starts re-inviting in a loop.
    """
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert _invite(tenant, ws, email="ops@acme.example.com")[0].status_code == 201
    original_expiry = (
        db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    ).reset_password_expires_at

    with patch("backend.tenants.members_service.send_email") as invite_mail, patch(
        "backend.auth.routes.send_email"
    ) as reset_mail:
        resp = tenant.post(
            "/auth/forgot-password", json={"email": "ops@acme.example.com"}
        )
    assert resp.status_code == 200, resp.text
    # An invite went out, not a reset.
    assert invite_mail.call_count == 1
    assert reset_mail.call_count == 0
    assert "/accept-invite?token=" in invite_mail.call_args.kwargs["body"]

    db_session.expire_all()
    invitee = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    # Re-issued on the invite's own clock, not shortened to the reset's hour.
    assert invitee.reset_password_expires_at > _utcnow() + timedelta(days=6)
    assert original_expiry is not None
    # And the link in that mail is the one that works.
    token = invite_mail.call_args.kwargs["body"].split("token=")[1].split()[0]
    assert _accept(tenant, token).status_code == 200


def test_forgot_password_still_resets_for_a_real_member(
    tenant: TestClient, db_session: Session
) -> None:
    """The invite branch must not swallow ordinary password resets."""
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")

    with patch("backend.tenants.members_service.send_email") as invite_mail, patch(
        "backend.auth.routes.send_email"
    ) as reset_mail:
        resp = tenant.post(
            "/auth/forgot-password", json={"email": "ops@acme.example.com"}
        )
    assert resp.status_code == 200
    assert invite_mail.call_count == 0
    assert reset_mail.call_count == 1
    assert "/reset-password?token=" in reset_mail.call_args.kwargs["body"]


# ---------------------------------------------------------------------------
# Invitations nobody accepted do not linger
# ---------------------------------------------------------------------------


def test_expired_invitations_are_purged_and_free_the_address(
    tenant: TestClient, db_session: Session
) -> None:
    from backend.jobs.expired_invitations_purge import purge_expired_invitations

    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert _invite(tenant, ws, email="typo@exmaple.example.com")[0].status_code == 201
    assert _invite(tenant, ws, email="live@acme.example.com")[0].status_code == 201
    _onboard_operator(tenant, db_session, ws, email="joined@acme.example.com")

    stale = (
        db_session.query(User).filter(User.email == "typo@exmaple.example.com").one()
    )
    stale.reset_password_expires_at = _utcnow() - timedelta(minutes=1)
    db_session.commit()

    assert purge_expired_invitations(db_session) == 1

    db_session.expire_all()
    # The dead invitation is gone; the live one and the accepted one are not.
    assert (
        db_session.query(User).filter(User.email == "typo@exmaple.example.com").first()
        is None
    )
    assert (
        db_session.query(User).filter(User.email == "live@acme.example.com").first()
        is not None
    )
    assert (
        db_session.query(User).filter(User.email == "joined@acme.example.com").first()
        is not None
    )
    # The mistyped address can be used by whoever really owns it.
    with patch("backend.auth.routes.send_email"):
        assert (
            tenant.post(
                "/auth/register",
                json={"email": "typo@exmaple.example.com", "password": PASSWORD},
            ).status_code
            == 200
        )


def test_the_purge_leaves_a_signup_in_progress_alone(
    tenant: TestClient, db_session: Session
) -> None:
    """An unverified account with no workspace is someone mid-signup, not an
    invitation — deleting it would destroy an account being created."""
    from backend.jobs.expired_invitations_purge import purge_expired_invitations

    with patch("backend.auth.routes.send_email"):
        assert (
            tenant.post(
                "/auth/register",
                json={"email": "signing.up@acme.example.com", "password": PASSWORD},
            ).status_code
            == 200
        )
    registrant = (
        db_session.query(User).filter(User.email == "signing.up@acme.example.com").one()
    )
    assert registrant.tenant_id is None
    # Even with a long-expired reset token of their own.
    registrant.reset_password_token = uuid.uuid4().hex
    registrant.reset_password_expires_at = _utcnow() - timedelta(days=30)
    db_session.commit()

    assert purge_expired_invitations(db_session) == 0
    db_session.expire_all()
    assert (
        db_session.query(User).filter(User.email == "signing.up@acme.example.com").first()
        is not None
    )


def test_purging_an_owner_invitation_cannot_strand_a_workspace(
    tenant: TestClient, db_session: Session
) -> None:
    """The ordering the two fixes depend on, asserted rather than assumed."""
    from backend.jobs.expired_invitations_purge import purge_expired_invitations
    from backend.tenants.members_service import count_owners

    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    assert _invite(tenant, ws, email="never@acme.example.com", role="owner")[
        0
    ].status_code == 201
    pending = db_session.query(User).filter(User.email == "never@acme.example.com").one()
    pending.reset_password_expires_at = _utcnow() - timedelta(minutes=1)
    db_session.commit()

    before = count_owners(ws.tenant_id, db_session)
    assert purge_expired_invitations(db_session) == 1
    db_session.expire_all()
    assert count_owners(ws.tenant_id, db_session) == before == 1
    assert tenant.get("/tenants/members", headers=ws.auth).status_code == 200


# ---------------------------------------------------------------------------
# A role this build has never heard of
# ---------------------------------------------------------------------------


def test_an_unknown_role_degrades_instead_of_breaking_the_dashboard(
    tenant: TestClient, db_session: Session
) -> None:
    """``users.role`` is a free string; the response type must not be closed.

    Reachable without a bug: deploy a build that adds a third role, let it
    write one row, roll back. A 500 here takes out the whole app shell, which
    calls ``/tenants/me`` on mount.
    """
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")

    # First, while they are still an owner: the API refuses to write a role
    # this build does not implement. Requests stay closed; only reads open up.
    assert (
        tenant.post(
            "/tenants/members/invite",
            headers=ws.auth,
            json={"email": "x@acme.example.com", "role": "auditor"},
        ).status_code
        == 422
    )

    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    owner.role = "auditor"
    db_session.commit()

    me = tenant.get("/tenants/me", headers=ws.auth)
    assert me.status_code == 200, me.text
    # Reported truthfully, and it buys no privilege: every check tests for
    # "owner" explicitly, so an unknown role fails closed.
    assert me.json()["role"] == "auditor"
    assert tenant.get("/tenants/members", headers=ws.auth).status_code == 403
