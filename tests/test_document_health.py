"""Tests for document health check (FI-032)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.documents.service import _compute_health_score, _normalize_warnings, run_document_health_check
from backend.models import Tenant, Document, DocumentStatus, DocumentType, Embedding, User
from tests.conftest import register_and_verify_user


def _create_ready_document(
    db_session: Session,
    *,
    email: str,
    filename: str,
    parsed_text: str,
    file_type: DocumentType = DocumentType.markdown,
) -> Document:
    user = User(
        email=email,
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(name="Health Tenant")
    db_session.add(tenant)
    db_session.flush()
    doc = Document(
        tenant_id=tenant.id,
        filename=filename,
        file_type=file_type,
        parsed_text=parsed_text,
        status=DocumentStatus.ready,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_compute_health_score_penalties() -> None:
    assert _compute_health_score([]) == 100
    assert _compute_health_score([{"severity": "high"}]) == 80
    assert _compute_health_score([{"severity": "medium"}]) == 90
    assert _compute_health_score([{"severity": "low"}]) == 95
    assert (
        _compute_health_score(
            [
                {"severity": "high"},
                {"severity": "medium"},
                {"severity": "low"},
            ]
        )
        == 65
    )
    assert _compute_health_score([{"severity": "high"}] * 10) == 0


def test_normalize_warnings_filters_invalid() -> None:
    raw = [
        {"type": "poor_structure", "severity": "medium", "message": "ok"},
        {"type": "bad_type", "severity": "medium", "message": "skip"},
        {"severity": "low", "message": "missing type"},
    ]
    out = _normalize_warnings(raw)
    assert len(out) == 1
    assert out[0]["type"] == "poor_structure"


def test_get_document_health_404_when_null(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(tenant, db_session, email="health404@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Health Tenant"},
    )
    md_content = b"# Doc\n\nSome text."
    up = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("h.md", md_content, "text/markdown")},
    )
    assert up.status_code == 201
    doc_id = up.json()["id"]
    r = tenant.get(
        f"/documents/{doc_id}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert "not yet available" in r.json()["detail"].lower()


def test_document_health_ownership_enforced(tenant: TestClient, db_session: Session) -> None:
    token_a = register_and_verify_user(tenant, db_session, email="owner_a@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Tenant A"},
    )
    up = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("a.md", b"# A\n\nText.", "text/markdown")},
    )
    doc_id = up.json()["id"]

    token_b = register_and_verify_user(tenant, db_session, email="owner_b@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )
    r_health = tenant.get(
        f"/documents/{doc_id}/health",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_health.status_code == 404
    r_run = tenant.post(
        f"/documents/{doc_id}/health/run",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_run.status_code == 404


def test_run_document_health_check_flags_short_document(db_session: Session) -> None:
    doc = _create_ready_document(
        db_session,
        email="short@example.com",
        filename="f.md",
        parsed_text="# Title\n\nTiny note.",
    )

    result = run_document_health_check(doc.id, db_session)

    assert result["score"] == 80
    assert [warning["type"] for warning in result["warnings"]] == ["empty_or_too_short"]


def test_run_document_health_check_uses_parsed_text_not_embedding_chunks(db_session: Session) -> None:
    structured_text = "\n".join(
        [
            "# TurboFlare",
            "",
            "## Setup",
            "",
            (
                "Install the agent, verify DNS delegation, confirm the SSL status, and review cache rules. "
                "Map the origin IP, update the registrar settings, and check the readiness states in the panel. "
                "Use the troubleshooting section if propagation takes longer than expected. "
                "Document the exact registrar fields, the expected propagation timing, and the recovery steps for failed validation. "
            ).strip(),
            "",
            "## SSL",
            "",
            (
                "Enable HTTPS, validate the certificate, verify redirect behavior, and confirm fallback settings. "
                "Review stale cache behavior, query string settings, and cookie-aware cache options. "
                "Test the final domain over HTTPS after traffic is switched. "
                "Record the expected panel statuses, the final smoke checks, and the rollback steps if traffic cutover fails. "
            ).strip(),
        ]
    )
    doc = _create_ready_document(
        db_session,
        email="structure@example.com",
        filename="structured.md",
        parsed_text=structured_text,
    )
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text=("Unstructured content without headings. " * 80).strip(),
            vector=None,
            metadata_json={},
        )
    )
    db_session.commit()

    result = run_document_health_check(doc.id, db_session)

    assert result["warnings"] == []
    assert result["score"] == 100


def test_run_document_health_check_flags_poor_structure(db_session: Session) -> None:
    doc = _create_ready_document(
        db_session,
        email="poor-structure@example.com",
        filename="long.md",
        parsed_text="# Big Guide\n\n" + ("One long section without subheadings. " * 140),
    )

    result = run_document_health_check(doc.id, db_session)

    assert "poor_structure" in [warning["type"] for warning in result["warnings"]]


def test_run_document_health_check_flags_incomplete_section(db_session: Session) -> None:
    doc = _create_ready_document(
        db_session,
        email="incomplete@example.com",
        filename="todo.md",
        parsed_text="# Guide\n\n## Next steps\n\nTODO: add the final verification workflow.",
    )

    result = run_document_health_check(doc.id, db_session)

    assert "incomplete_section" in [warning["type"] for warning in result["warnings"]]


def test_run_document_health_check_allows_nested_subsections(db_session: Session) -> None:
    doc = _create_ready_document(
        db_session,
        email="nested-sections@example.com",
        filename="nested.md",
        parsed_text="# Guide\n\n## Setup\n\n### Step 1\n\nFollow the setup instructions here.",
    )

    result = run_document_health_check(doc.id, db_session)

    assert "incomplete_section" not in [
        warning["type"] for warning in result["warnings"]
    ]


def test_run_document_health_check_flags_empty_h2_section(db_session: Session) -> None:
    doc = _create_ready_document(
        db_session,
        email="empty-h2@example.com",
        filename="empty-h2.md",
        parsed_text="# Guide\n\n## Setup\n\n## Next steps\n\nThe next section has body text.",
    )

    result = run_document_health_check(doc.id, db_session)

    assert "incomplete_section" in [warning["type"] for warning in result["warnings"]]


def test_run_document_health_check_flags_parse_issue(db_session: Session) -> None:
    doc = _create_ready_document(
        db_session,
        email="parse@example.com",
        filename="broken.pdf",
        parsed_text="Valid intro text.\n\nBroken field: \ufffd\ufffd\ufffd",
        file_type=DocumentType.pdf,
    )

    result = run_document_health_check(doc.id, db_session)

    assert "parse_or_extraction_issue" in [warning["type"] for warning in result["warnings"]]


def test_run_document_health_check_flags_low_information_density(db_session: Session) -> None:
    repetitive_line = "Status page overview and status page overview for every status page visitor."
    doc = _create_ready_document(
        db_session,
        email="density@example.com",
        filename="repetitive.md",
        parsed_text="# Status\n\n" + "\n".join(repetitive_line for _ in range(16)),
    )

    result = run_document_health_check(doc.id, db_session)

    assert "low_information_density" in [warning["type"] for warning in result["warnings"]]


def test_get_health_after_run_via_api_without_openai_key(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(tenant, db_session, email="apihealth@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "API Health"},
    )
    up = tenant.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "x.md",
                (
                    b"# Guide\n\n"
                    b"TODO: finish the rollout checklist after validating the DNS delegation, SSL status, "
                    b"cache behavior, and final HTTPS verification for the production domain. "
                    b"Confirm each step in the panel before publishing the guide."
                ),
                "text/markdown",
            )
        },
    )
    doc_id = up.json()["id"]
    run = tenant.post(
        f"/documents/{doc_id}/health/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.status_code == 200
    data = run.json()
    assert data["score"] == 80
    assert "incomplete_section" in [warning["type"] for warning in data["warnings"]]
    get = tenant.get(
        f"/documents/{doc_id}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get.status_code == 200
    assert get.json()["score"] == 80
