"""Tests for document health check (FI-032)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.documents.service import (
    _compute_health_score,
    _expects_contact_info,
    _normalize_warnings,
    run_document_health_check,
)
from backend.models import Client, Document, DocumentStatus, DocumentType, User
from tests.conftest import register_and_verify_user, set_client_openai_key


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
        {"type": "no_examples", "severity": "medium", "message": "ok"},
        {"type": "bad_type", "severity": "medium", "message": "skip"},
        {"severity": "low", "message": "missing type"},
    ]
    out = _normalize_warnings(raw)
    assert len(out) == 1
    assert out[0]["type"] == "no_examples"


def test_expects_contact_info_only_for_support_like_docs() -> None:
    assert _expects_contact_info("support-guide.md", "How to contact support if billing fails.")
    assert _expects_contact_info("product.md", "FAQ: if the fix fails, contact us for technical support.")
    assert not _expects_contact_info("product.md", "FAQ: how do I reset my password?")
    assert not _expects_contact_info("api-reference.md", "List users endpoint and response schema.")
    assert not _expects_contact_info("features.md", "This page describes analytics dashboards and filters.")
    assert not _expects_contact_info("product.md", "This helpful overview covers onboarding and setup.")
    assert not _expects_contact_info("glossary.md", "The term afaq appears here as sample data only.")


def test_get_document_health_404_when_null(client: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(client, db_session, email="health404@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Health Client"},
    )
    md_content = b"# Doc\n\nSome text."
    up = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("h.md", md_content, "text/markdown")},
    )
    assert up.status_code == 201
    doc_id = up.json()["id"]
    r = client.get(
        f"/documents/{doc_id}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert "not yet available" in r.json()["detail"].lower()


def test_document_health_ownership_enforced(client: TestClient, db_session: Session) -> None:
    token_a = register_and_verify_user(client, db_session, email="owner_a@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    set_client_openai_key(client, token_a)
    up = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("a.md", b"# A\n\nText.", "text/markdown")},
    )
    doc_id = up.json()["id"]

    token_b = register_and_verify_user(client, db_session, email="owner_b@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )
    r_health = client.get(
        f"/documents/{doc_id}/health",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_health.status_code == 404
    r_run = client.post(
        f"/documents/{doc_id}/health/run",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_run.status_code == 404


def test_run_document_health_check_mocked_openai(db_session: Session, mock_openai_client: Mock) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[
            Mock(
                message=Mock(
                    content='{"warnings": [{"type": "no_examples", "severity": "high", "message": "Too abstract."}]}'
                )
            )
        ],
    )
    user = User(
        email="unit@example.com",
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    cl = Client(user_id=user.id, name="C", api_key="k" * 32)
    db_session.add(cl)
    db_session.flush()
    doc = Document(
        client_id=cl.id,
        filename="f.md",
        file_type=DocumentType.markdown,
        parsed_text="# Hello\n\nContent here.",
        status=DocumentStatus.ready,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    result = run_document_health_check(doc.id, db_session, "sk-test")
    assert result["score"] == 80
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["type"] == "no_examples"
    db_session.refresh(doc)
    assert doc.health_status is not None
    assert doc.health_status.get("score") == 80


def test_run_document_health_check_prompt_skips_contact_rule_for_regular_docs(
    db_session: Session, mock_openai_client: Mock
) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content='{"warnings": []}'))],
    )
    user = User(
        email="prompt@example.com",
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    cl = Client(user_id=user.id, name="Prompt Client", api_key="k" * 32)
    db_session.add(cl)
    db_session.flush()
    doc = Document(
        client_id=cl.id,
        filename="api-reference.md",
        file_type=DocumentType.markdown,
        parsed_text="Users endpoint reference with request and response fields.",
        status=DocumentStatus.ready,
    )
    db_session.add(doc)
    db_session.commit()

    run_document_health_check(doc.id, db_session, "sk-test")

    prompt = mock_openai_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert (
        "Do not report missing_contact_info unless the document is clearly about help, support, contact, FAQ, or troubleshooting."
        not in prompt
    )
    assert (
        "for a document that is clearly about help, support, contact, or troubleshooting"
        not in prompt
    )


def test_run_document_health_check_prompt_includes_contact_rule_for_support_docs(
    db_session: Session, mock_openai_client: Mock
) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content='{"warnings": []}'))],
    )
    user = User(
        email="supportprompt@example.com",
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    cl = Client(user_id=user.id, name="Support Prompt Client", api_key="k" * 32)
    db_session.add(cl)
    db_session.flush()
    doc = Document(
        client_id=cl.id,
        filename="support-center.md",
        file_type=DocumentType.markdown,
        parsed_text="Support guide for customers who need help with account access.",
        status=DocumentStatus.ready,
    )
    db_session.add(doc)
    db_session.commit()

    run_document_health_check(doc.id, db_session, "sk-test")

    prompt = mock_openai_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "for a document that is clearly about help, support, contact, or troubleshooting" in prompt
    assert (
        "Do not report missing_contact_info unless the document is clearly about help, support, contact, FAQ, or troubleshooting."
        in prompt
    )


@pytest.mark.parametrize(
    "bad_content",
    ["not json", '{"warnings": "nope"}'],
)
def test_run_document_health_check_parse_failure_stores_error(
    db_session: Session, mock_openai_client: Mock, bad_content: str
) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content=bad_content))],
    )
    user = User(
        email="err@example.com",
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    cl = Client(user_id=user.id, name="C2", api_key="a" * 32)
    db_session.add(cl)
    db_session.flush()
    doc = Document(
        client_id=cl.id,
        filename="e.md",
        file_type=DocumentType.markdown,
        parsed_text="Some text.",
        status=DocumentStatus.ready,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    result = run_document_health_check(doc.id, db_session, "sk-test")
    assert result.get("error") == "health check failed"
    assert result.get("score") is None
    db_session.refresh(doc)
    assert doc.health_status is not None
    assert doc.health_status.get("error") == "health check failed"


def test_get_health_after_run_via_api(client: TestClient, db_session: Session, mock_openai_client: Mock) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[
            Mock(
                message=Mock(
                    content='{"warnings": [{"type": "poor_structure", "severity": "low", "message": "Long blocks."}]}'
                )
            )
        ],
    )
    token = register_and_verify_user(client, db_session, email="apihealth@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "API Health"},
    )
    set_client_openai_key(client, token)
    up = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("x.md", b"# X\n\nWords.", "text/markdown")},
    )
    doc_id = up.json()["id"]
    run = client.post(
        f"/documents/{doc_id}/health/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.status_code == 200
    data = run.json()
    assert data["score"] == 95
    get = client.get(
        f"/documents/{doc_id}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get.status_code == 200
    assert get.json()["score"] == 95
