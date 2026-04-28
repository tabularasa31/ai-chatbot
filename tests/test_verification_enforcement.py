"""Tests for email verification enforcement on mutating endpoints."""

from __future__ import annotations

from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.embeddings.service import chunk_text


def _get_unverified_user_token(tenant: TestClient, db_session=None) -> str:
    """Register user (is_verified=False), return JWT via create_token_for_user."""
    from backend.auth.service import create_token_for_user
    from backend.core.security import hash_password
    from backend.models import User

    assert db_session is not None, "db_session required for _get_unverified_user_token"
    user = User(
        email="unverified@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token, _ = create_token_for_user(user)
    return token


def _get_verified_user_token(tenant: TestClient, db_session) -> str:
    """Register user, verify via /auth/verify-email, return JWT."""
    from tests.conftest import register_and_verify_user

    return register_and_verify_user(tenant, db_session, email="verified@example.com")


@patch("backend.auth.routes.send_email")
def test_get_me_forbidden_for_unverified_user(
    mock_send_email: Mock, tenant: TestClient, db_session
) -> None:
    """GET /auth/me with unverified user JWT → 403."""
    token = _get_unverified_user_token(tenant, db_session)
    response = tenant.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified."


@patch("backend.auth.routes.send_email")
def test_create_client_forbidden_for_unverified_user(
    mock_send_email: Mock, tenant: TestClient, db_session
) -> None:
    """POST /tenants with unverified user → 403."""
    token = _get_unverified_user_token(tenant, db_session)
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Tenant"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified."


@patch("backend.auth.routes.send_email")
def test_create_client_allowed_for_verified_user(
    mock_send_email: Mock,
    tenant: TestClient,
    db_session,
) -> None:
    """POST /tenants with verified user → 201, tenant created."""
    token = _get_verified_user_token(tenant, db_session)
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Verified Tenant"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Verified Tenant"
    assert "id" in data
    assert "api_key" in data


@patch("backend.auth.routes.send_email")
def test_upload_document_forbidden_for_unverified_user(
    mock_send_email: Mock,
    tenant: TestClient,
    db_session,
) -> None:
    """POST /documents with unverified user → 403."""
    token = _get_unverified_user_token(tenant, db_session)
    md_content = b"# Test\n\nContent."
    response = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.md", md_content, "text/markdown")},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified."


@patch("backend.auth.routes.send_email")
def test_upload_document_allowed_for_verified_user(
    mock_send_email: Mock,
    tenant: TestClient,
    db_session,
) -> None:
    """POST /documents with verified user → 201."""
    token = _get_verified_user_token(tenant, db_session)
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Doc Tenant"},
    )
    md_content = b"# Test\n\nContent."
    response = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.md", md_content, "text/markdown")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "test.md"


@patch("backend.auth.routes.send_email")
def test_create_embeddings_forbidden_for_unverified_user(
    mock_send_email: Mock,
    tenant: TestClient,
    db_session,
) -> None:
    """POST /embeddings/documents/{id} with unverified user → 403."""
    from backend.auth.service import create_token_for_user
    from backend.tenants.service import create_tenant
    from backend.core.security import hash_password
    from backend.models import Document, DocumentStatus, DocumentType, User

    user = User(
        email="emb_unv@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    token, _ = create_token_for_user(user)
    cl, _ = create_tenant(user.id, "Emb Tenant", db_session)
    cl.openai_api_key = "sk-test"
    db_session.commit()
    db_session.refresh(cl)

    doc = Document(
        tenant_id=cl.id,
        filename="emb.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Some text for embeddings.",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    response = tenant.post(
        f"/embeddings/documents/{doc.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified."


@patch("backend.auth.routes.send_email")
@patch("backend.embeddings.service.get_openai_client")
def test_create_embeddings_allowed_for_verified_user(
    mock_get_openai: Mock,
    mock_send_email: Mock,
    tenant: TestClient,
    db_session,
) -> None:
    """POST /embeddings/documents/{id} with verified user → 200, embeddings created."""
    from tests.conftest import register_and_verify_user, set_client_openai_key

    token = register_and_verify_user(tenant, db_session, email="emb_verified@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Emb Tenant"},
    )
    r = tenant.patch(
        "/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"openai_api_key": "sk-test"},
    )
    assert r.status_code == 200

    md_content = b"# Test\n\n" + b"Lorem ipsum. " * 50
    chunks = chunk_text(md_content.decode(), chunk_size=500, overlap_sentences=1)
    mock_client = Mock()
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536) for _ in range(len(chunks))]
    )
    mock_get_openai.return_value = mock_client

    upload_resp = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("emb.md", md_content, "text/markdown")},
    )
    doc_id = upload_resp.json()["id"]

    response = tenant.post(
        f"/embeddings/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["status"] == "embedding"
