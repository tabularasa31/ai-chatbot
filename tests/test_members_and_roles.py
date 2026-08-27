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

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth.service import create_token_for_user
from backend.models import Chat, User
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
    with patch("backend.tenants.members_routes.send_email") as sent:
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
    assert body["invite_sent"] is True
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
        tenant.get("/tenants/me/support-settings", headers=headers),
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


def test_the_last_owner_cannot_be_removed_or_demoted(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _make_workspace(tenant, db_session, email="owner@acme.example.com")
    _onboard_operator(tenant, db_session, ws, email="ops@acme.example.com")

    demote = tenant.patch(
        f"/tenants/members/{ws.owner_id}", headers=ws.auth, json={"role": "operator"}
    )
    assert demote.status_code == 400, demote.text
    assert "last owner" in demote.json()["detail"].lower()

    db_session.expire_all()
    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    assert owner.role == "owner"


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
    # With a second owner in place the demotion is now legal.
    assert (
        tenant.patch(
            f"/tenants/members/{ws.owner_id}",
            headers=ws.auth,
            json={"role": "operator"},
        ).status_code
        == 200
    )
    # And the demoted founder is an operator from the next request on.
    assert tenant.get("/tenants/members", headers=ws.auth).status_code == 403
    assert tenant.get("/tenants/members", headers=_auth(op_token)).status_code == 200


def test_a_removed_member_loses_access(
    tenant: TestClient, db_session: Session
) -> None:
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
    member = db_session.query(User).filter(User.email == "ops@acme.example.com").one()
    assert member.tenant_id is None
    # Detached, not deleted: the account survives so transcripts keep naming it.
    assert member.role == "owner"

    headers = _auth(op_token)
    assert tenant.post(f"/operator/chats/{chat.id}/take", headers=headers).status_code == 404
    assert tenant.get("/tenants/members", headers=headers).status_code == 404
    assert tenant.get("/tenants/me", headers=headers).status_code == 404


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
    """``users.tenant_id`` is nullable, so this principal really exists.

    Their ``role`` column still reads "owner" — its default — which is
    exactly why the role check must look at the membership first.
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
