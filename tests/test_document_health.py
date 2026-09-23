"""Tests for document health check (FI-032)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.documents.service import (
    _compute_health_score,
    _normalize_warnings,
    run_document_health_check,
)
from backend.models import (
    Tenant,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    User,
)
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


_STRUCTURED_TEXT = "\n".join(
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

_REPETITIVE_LINE = "Status page overview and status page overview for every status page visitor."


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


def test_get_document_health_404_when_null(
    tenant: TestClient, db_session: Session
) -> None:
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


def test_document_health_ownership_enforced(
    tenant: TestClient, db_session: Session
) -> None:
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


@pytest.mark.parametrize(
    "parsed_text, file_type, embedding_chunk_text, expected_score, must_include, must_exclude",
    [
        pytest.param(
            "# Title\n\nTiny note.",
            DocumentType.markdown,
            None,
            80,
            ["empty_or_too_short"],
            [],
            id="short_document",
        ),
        pytest.param(
            _STRUCTURED_TEXT,
            DocumentType.markdown,
            ("Unstructured content without headings. " * 80).strip(),
            100,
            [],
            ["poor_structure", "incomplete_section", "empty_or_too_short", "low_information_density"],
            id="structure_ignores_embedding_chunks",
        ),
        pytest.param(
            "# Big Guide\n\n" + ("One long section without subheadings. " * 140),
            DocumentType.markdown,
            None,
            None,
            ["poor_structure"],
            [],
            id="poor_structure",
        ),
        pytest.param(
            "# Guide\n\n## Next steps\n\nRun the verification workflow:\n\n```bash\nmake verify\n",
            DocumentType.markdown,
            None,
            None,
            ["incomplete_section"],
            [],
            id="unclosed_code_fence",
        ),
        pytest.param(
            "# Guide\n\n## Next steps\n\nThe remaining verification steps are:",
            DocumentType.markdown,
            None,
            None,
            ["incomplete_section"],
            [],
            id="text_ends_mid_thought",
        ),
        pytest.param(
            "# Guide\n\n## Setup\n\n### Step 1\n\nFollow the setup instructions here.",
            DocumentType.markdown,
            None,
            None,
            [],
            ["incomplete_section"],
            id="nested_subsections_allowed",
        ),
        pytest.param(
            "# Guide\n\n## Setup\n\n## Next steps\n\nThe next section has body text.",
            DocumentType.markdown,
            None,
            None,
            ["incomplete_section"],
            [],
            id="empty_h2_section",
        ),
        pytest.param(
            "Valid intro text.\n\nBroken field: ���",
            DocumentType.pdf,
            None,
            None,
            ["parse_or_extraction_issue"],
            [],
            id="parse_issue",
        ),
        pytest.param(
            "# Status\n\n" + "\n".join([_REPETITIVE_LINE] * 16),
            DocumentType.markdown,
            None,
            None,
            ["low_information_density"],
            [],
            id="low_information_density",
        ),
    ],
)
def test_run_document_health_check_flags(
    db_session: Session,
    parsed_text: str,
    file_type: DocumentType,
    embedding_chunk_text: str | None,
    expected_score: int | None,
    must_include: list[str],
    must_exclude: list[str],
) -> None:
    doc = _create_ready_document(
        db_session,
        email="health-flags@example.com",
        filename="f.md",
        parsed_text=parsed_text,
        file_type=file_type,
    )
    if embedding_chunk_text is not None:
        db_session.add(
            Embedding(document_id=doc.id, chunk_text=embedding_chunk_text, vector=None, metadata_json={})
        )
        db_session.commit()

    result = run_document_health_check(doc.id, db_session)
    types = [warning["type"] for warning in result["warnings"]]

    for warning_type in must_include:
        assert warning_type in types
    for warning_type in must_exclude:
        assert warning_type not in types
    if expected_score is not None:
        assert result["score"] == expected_score


def test_get_health_after_run_via_api_without_openai_key(
    tenant: TestClient, db_session: Session
) -> None:
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
                    b"Finish the rollout checklist after validating the DNS delegation, SSL status, "
                    b"cache behavior, and final HTTPS verification for the production domain. "
                    b"Run the verification command:\n\n"
                    b"```bash\nmake verify-domain\n"
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
