"""Miscellaneous tests that don't belong to a dedicated module."""

from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth import routes as auth_routes
from backend.models import Tenant, TenantFaq, User
from backend.tenant_knowledge import faq_service
from backend.tenant_knowledge.schemas import FaqCandidate
from backend.tenants.service import get_tenant_by_user
from tests.conftest import register_and_verify_user


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------


def test_fastapi_lifespan_triggers_graceful_shutdown(monkeypatch) -> None:
    import backend.main as backend_main

    calls: list[object] = []
    monkeypatch.setattr(
        backend_main,
        "gap_graceful_shutdown",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with TestClient(backend_main.app):
        pass

    assert calls == [((), {})]


# ---------------------------------------------------------------------------
# FAQ service
# ---------------------------------------------------------------------------


def _create_faq_tenant(db_session: Session, *, email: str) -> uuid.UUID:
    user = User(
        email=email,
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(name="FAQ Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant.id


def test_insert_new_faq_candidates_skips_low_confidence_and_duplicates(
    db_session: Session,
    mock_openai_client,
    monkeypatch,
) -> None:
    tenant_id = _create_faq_tenant(db_session, email="faq-service@example.com")
    mock_openai_client.embeddings.create.reset_mock()
    dedupe_results = iter([False, True])
    monkeypatch.setattr(
        faq_service,
        "_dedupe_existing_faq_by_similarity",
        lambda **kwargs: next(dedupe_results),
    )

    faq_service.insert_new_faq_candidates(
        db=db_session,
        tenant_id=tenant_id,
        faq_candidates=[
            FaqCandidate(
                question="How do billing exports work?",
                answer="Billing exports are generated from the exports page.",
                confidence=0.9,
                source="docs",
            ),
            FaqCandidate(
                question="What is this?",
                answer="Too vague to keep.",
                confidence=0.3,
                source="docs",
            ),
            FaqCandidate(
                question="Can confidence be missing?",
                answer="This candidate should be skipped before embedding.",
                confidence=None,
                source="docs",
            ),
            FaqCandidate(
                question="How do billing exports work?",
                answer="A duplicate answer should be skipped.",
                confidence=0.91,
                source="docs",
            ),
        ],
        api_key="test-key",
        document_id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
    )

    rows = db_session.query(TenantFaq).filter(TenantFaq.tenant_id == tenant_id).all()

    assert len(rows) == 1
    assert rows[0].question == "How do billing exports work?"
    assert mock_openai_client.embeddings.create.call_count == 2


# ---------------------------------------------------------------------------
# Privacy config
# ---------------------------------------------------------------------------


def test_get_privacy_defaults(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="privacy-default@example.com"
    )
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Privacy Tenant"},
    )

    resp = tenant.get(
        "/tenants/me/privacy", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert sorted(resp.json()["optional_entity_types"]) == ["ID_DOC", "IP", "URL_TOKEN"]


def test_put_privacy_updates_optional_entity_types(
    tenant: TestClient, db_session: Session
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="privacy-update@example.com"
    )
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Privacy Update Tenant"},
    )

    resp = tenant.put(
        "/tenants/me/privacy",
        headers={"Authorization": f"Bearer {token}"},
        json={"optional_entity_types": ["IP"]},
    )
    assert resp.status_code == 200
    assert resp.json()["optional_entity_types"] == ["IP"]

    resp2 = tenant.get(
        "/tenants/me/privacy", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code == 200
    assert resp2.json()["optional_entity_types"] == ["IP"]


# ---------------------------------------------------------------------------
# Alembic migration file checks
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1] / "backend" / "migrations" / "versions"
)
_MAX_REVISION_LEN = 32


def _load_revision(path: Path) -> str | None:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "revision", None)


def test_alembic_revisions_fit_version_num_limit() -> None:
    too_long = [
        (p.name, len(r))
        for p in sorted(_MIGRATIONS_DIR.glob("*.py"))
        if (r := _load_revision(p)) is not None and len(r) > _MAX_REVISION_LEN
    ]
    assert not too_long, f"Revision ids too long: {too_long}"


def test_alembic_revisions_are_unique() -> None:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        revision = _load_revision(path)
        assert revision is not None, f"{path.name} must define revision"
        if revision in seen:
            duplicates.append((revision, path.name))
        else:
            seen[revision] = path.name
    assert not duplicates, f"Duplicate revision ids: {duplicates}"


# ---------------------------------------------------------------------------
# Email verification flow
# ---------------------------------------------------------------------------


def test_signup_sets_verification_token(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        auth_routes,
        "send_email",
        lambda to, subject, body: calls.append((to, subject, body)),
    )

    response = tenant.post(
        "/auth/register",
        json={"email": "verify@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == "verify@example.com"

    user = db_session.query(User).filter(User.email == "verify@example.com").first()
    assert user is not None
    assert user.is_verified is False
    assert user.verification_token is not None
    assert user.verification_expires_at > dt.datetime.utcnow()


def test_verify_email_success(tenant: TestClient, db_session: Session) -> None:
    from backend.core.security import hash_password

    token = "abc123validtoken"
    user = User(
        email="toverify@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
        verification_token=token,
        verification_expires_at=dt.datetime.utcnow() + dt.timedelta(days=1),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = tenant.post("/auth/verify-email", json={"token": token})
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["expires_in"] == 24 * 60 * 60

    db_session.refresh(user)
    assert user.is_verified is True
    assert user.verification_token is None
    provisioned = get_tenant_by_user(user.id, db_session)
    assert provisioned is not None
    assert provisioned.name == "My Workspace"


def test_verify_email_invalid_token(tenant: TestClient) -> None:
    response = tenant.post(
        "/auth/verify-email", json={"token": "nonexistent-token-12345"}
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "invalid" in detail or "expired" in detail


def test_verify_email_expired_token(tenant: TestClient, db_session: Session) -> None:
    from backend.core.security import hash_password

    token = "expiredtoken123"
    user = User(
        email="expired@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
        verification_token=token,
        verification_expires_at=dt.datetime.utcnow() - dt.timedelta(hours=1),
    )
    db_session.add(user)
    db_session.commit()

    response = tenant.post("/auth/verify-email", json={"token": token})
    assert response.status_code == 400

    db_session.refresh(user)
    assert user.is_verified is False
    assert user.verification_token == token
