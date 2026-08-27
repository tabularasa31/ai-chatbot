"""Tests for the tenant subscription tier (`GET`/`PUT` /tenants/me/plan).

The tier is a stub: it is written, read back, and nothing in the product
consumes it. What these tests pin down is therefore the shape of the field
rather than any behaviour it drives — the default, both transitions, and who
is allowed to make them.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Tenant, TenantPlan, User
from tests.conftest import register_and_verify_user


def _create_tenant(client: TestClient, db: Session, email: str) -> str:
    """Register a verified owner with a tenant and return their JWT."""
    token = register_and_verify_user(client, db, email=email)
    response = client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Plan Tenant"},
    )
    assert response.status_code == 201, response.json()
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_plan_defaults_to_free(tenant: TestClient, db_session: Session) -> None:
    """A freshly created tenant is on the free tier."""
    token = _create_tenant(tenant, db_session, "plan-default@example.com")

    response = tenant.get("/tenants/me/plan", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == {"plan": "free"}


def test_owner_switches_to_pro_and_back(tenant: TestClient, db_session: Session) -> None:
    """The owner can turn the paid tier on and off again."""
    token = _create_tenant(tenant, db_session, "plan-toggle@example.com")

    up = tenant.put("/tenants/me/plan", headers=_auth(token), json={"plan": "pro"})
    assert up.status_code == 200, up.json()
    assert up.json() == {"plan": "pro"}
    assert tenant.get("/tenants/me/plan", headers=_auth(token)).json() == {"plan": "pro"}

    down = tenant.put("/tenants/me/plan", headers=_auth(token), json={"plan": "free"})
    assert down.status_code == 200, down.json()
    assert down.json() == {"plan": "free"}
    assert tenant.get("/tenants/me/plan", headers=_auth(token)).json() == {"plan": "free"}


def test_plan_change_persists_to_the_tenant_row(
    tenant: TestClient, db_session: Session
) -> None:
    """The switch writes the column, not just the response body."""
    token = _create_tenant(tenant, db_session, "plan-persist@example.com")

    tenant.put("/tenants/me/plan", headers=_auth(token), json={"plan": "pro"})

    user = db_session.query(User).filter(User.email == "plan-persist@example.com").first()
    row = db_session.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    db_session.refresh(row)
    assert row.plan == TenantPlan.pro.value


def test_non_owner_cannot_change_the_plan(
    tenant: TestClient, db_session: Session
) -> None:
    """A member reads the tier but cannot switch it."""
    token = _create_tenant(tenant, db_session, "plan-member@example.com")
    user = db_session.query(User).filter(User.email == "plan-member@example.com").first()
    user.role = "member"
    db_session.commit()

    read = tenant.get("/tenants/me/plan", headers=_auth(token))
    assert read.status_code == 200
    assert read.json() == {"plan": "free"}

    write = tenant.put("/tenants/me/plan", headers=_auth(token), json={"plan": "pro"})
    assert write.status_code == 403

    db_session.refresh(user)
    row = db_session.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    db_session.refresh(row)
    assert row.plan == TenantPlan.free.value


def test_unknown_plan_is_rejected(tenant: TestClient, db_session: Session) -> None:
    """An unrecognised tier never reaches the column."""
    token = _create_tenant(tenant, db_session, "plan-unknown@example.com")

    response = tenant.put(
        "/tenants/me/plan", headers=_auth(token), json={"plan": "enterprise"}
    )

    assert response.status_code == 422
    assert tenant.get("/tenants/me/plan", headers=_auth(token)).json() == {"plan": "free"}


def test_plan_requires_authentication(tenant: TestClient) -> None:
    """Neither endpoint is reachable without a token."""
    assert tenant.get("/tenants/me/plan").status_code in (401, 403)
    assert tenant.put("/tenants/me/plan", json={"plan": "pro"}).status_code in (401, 403)


def test_plan_literal_matches_the_enum() -> None:
    """The API Literal and the model enum must not drift apart.

    Adding a tier to one and forgetting the other would either hide it from
    the API or let the API accept a value the model does not know.
    """
    from typing import get_args

    from backend.tenants.schemas import TenantPlanLiteral

    assert set(get_args(TenantPlanLiteral)) == {p.value for p in TenantPlan}


def test_switching_the_plan_changes_nothing_else(
    tenant: TestClient, db_session: Session
) -> None:
    """The stub is inert: the tier is the only thing the switch touches.

    Guards the "do not gate anything on it yet" boundary from the other side
    — if a future change starts deriving tenant state from the plan, this
    notices.
    """
    token = _create_tenant(tenant, db_session, "plan-inert@example.com")
    before = tenant.get("/tenants/me", headers=_auth(token)).json()
    support_before = tenant.get(
        "/tenants/me/support-settings", headers=_auth(token)
    ).json()

    tenant.put("/tenants/me/plan", headers=_auth(token), json={"plan": "pro"})

    after = tenant.get("/tenants/me", headers=_auth(token)).json()
    support_after = tenant.get(
        "/tenants/me/support-settings", headers=_auth(token)
    ).json()
    assert {k: v for k, v in after.items() if k != "updated_at"} == {
        k: v for k, v in before.items() if k != "updated_at"
    }
    assert support_after == support_before
