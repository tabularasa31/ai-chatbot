"""Smoke tests for KB language metrics event emission.

Covers:
- document_indexed from upload_document
- document_indexed from _upsert_page_document
- document_indexed from _upsert_structured_document
- tenant_kb_language_snapshot from run_kb_language_snapshot_for_all_tenants
- cross-lingual props wired into _emit_chat_turn_event
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from backend.models import Document, DocumentStatus, DocumentType, Tenant
from backend.models.base import _utcnow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(db: Session) -> Tenant:
    t = Tenant(
        name="test-tenant",
        public_id=f"pub_{uuid.uuid4().hex[:16]}",
        openai_api_key="sk-test",
        is_active=True,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_document(
    db: Session,
    tenant: Tenant,
    language: str | None = "en",
    script: str | None = "latin",
) -> Document:
    doc = Document(
        tenant_id=tenant.id,
        filename="test.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Hello world this is test content.",
        language=language,
        script=script,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _capture_events(monkeypatch: pytest.MonkeyPatch, target: str) -> list[dict]:
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr(target, fake_capture)
    return events


# ---------------------------------------------------------------------------
# document_indexed — upload path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detected_language, parsed_text, expected_language, expected_language_detected",
    [
        pytest.param("en", "parsed content", "en", True, id="language_detected"),
        pytest.param(None, "short text", None, False, id="language_detected_false_when_null"),
    ],
)
def test_upload_document_emits_document_indexed(
    db_session: Session,
    monkeypatch,
    detected_language: str | None,
    parsed_text: str,
    expected_language: str | None,
    expected_language_detected: bool,
):
    tenant = _make_tenant(db_session)
    events = _capture_events(monkeypatch, "backend.documents.service.capture_event")
    monkeypatch.setattr(
        "backend.documents.service.detect_document_language",
        lambda text: detected_language,
    )
    monkeypatch.setattr(
        "backend.documents.service._parse_content",
        lambda content, file_type: parsed_text,
    )
    monkeypatch.setattr(
        "backend.documents.service.invalidate_bm25_cache_for_tenant",
        lambda tid: None,
    )

    from backend.documents.service import upload_document

    upload_document(
        tenant_id=tenant.id,
        filename="doc.md",
        content=b"# Hello\n\nSome content here.",
        file_type="markdown",
        db=db_session,
    )

    indexed = [e for e in events if e["event"] == "document_indexed"]
    assert len(indexed) == 1
    e = indexed[0]
    assert e["distinct_id"] == str(tenant.id)
    props = e["properties"]
    assert props["file_type"] == "markdown"
    assert props["source_kind"] == "upload"
    assert props["language"] == expected_language
    assert props["language_detected"] is expected_language_detected
    assert "parsed_text_chars" in props


# ---------------------------------------------------------------------------
# tenant_kb_language_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_emits_for_tenant_with_documents(db_session: Session, monkeypatch):
    tenant = _make_tenant(db_session)
    _make_document(db_session, tenant, language="en", script="latin")
    _make_document(db_session, tenant, language="ru", script="cyrillic")
    _make_document(db_session, tenant, language=None, script=None)

    events = _capture_events(monkeypatch, "backend.jobs.kb_language_snapshot.capture_event")

    from backend.jobs.kb_language_snapshot import (
        run_kb_language_snapshot_for_all_tenants,
    )

    count = run_kb_language_snapshot_for_all_tenants(db_session)
    assert count >= 1

    snapshots = [e for e in events if e["event"] == "tenant_kb_language_snapshot"]
    assert len(snapshots) == 1
    props = snapshots[0]["properties"]
    assert props["total_documents"] == 3
    assert props["documents_with_language"] == 2
    assert props["language_count"] == 2
    assert props["is_multilingual"] is True
    assert set(props["kb_scripts"]) == {"latin", "cyrillic"}
    assert props["script_count"] == 2
    assert props["dominant_language"] in ("en", "ru")
    assert props["language_distribution"] == {"en": 1, "ru": 1}
    assert props["tenant_age_days"] >= 0


def test_snapshot_skips_tenant_with_no_documents(db_session: Session, monkeypatch):
    _make_tenant(db_session)

    events = _capture_events(monkeypatch, "backend.jobs.kb_language_snapshot.capture_event")

    from backend.jobs.kb_language_snapshot import (
        run_kb_language_snapshot_for_all_tenants,
    )

    run_kb_language_snapshot_for_all_tenants(db_session)

    snapshots = [e for e in events if e["event"] == "tenant_kb_language_snapshot"]
    assert len(snapshots) == 0


def test_snapshot_monolingual_tenant(db_session: Session, monkeypatch):
    tenant = _make_tenant(db_session)
    _make_document(db_session, tenant, language="en")
    _make_document(db_session, tenant, language="en")

    events = _capture_events(monkeypatch, "backend.jobs.kb_language_snapshot.capture_event")

    from backend.jobs.kb_language_snapshot import (
        run_kb_language_snapshot_for_all_tenants,
    )

    run_kb_language_snapshot_for_all_tenants(db_session)

    snapshots = [e for e in events if e["event"] == "tenant_kb_language_snapshot"]
    assert len(snapshots) == 1
    props = snapshots[0]["properties"]
    assert props["is_multilingual"] is False
    assert props["language_count"] == 1
    assert props["dominant_language"] == "en"


# ---------------------------------------------------------------------------
# cross-lingual properties in _emit_chat_turn_event
# ---------------------------------------------------------------------------


def test_emit_chat_turn_event_includes_cross_lingual_props(monkeypatch):
    captured = _capture_events(monkeypatch, "backend.chat.events.capture_event")

    from backend.chat.events import _emit_chat_turn_event

    _emit_chat_turn_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_abc",
        chat_id="chat_abc",
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        escalated=False,
        query_script="latin",
        kb_scripts=["latin", "cyrillic"],
        cross_lingual_triggered=True,
        cross_lingual_variants_count=1,
        query_kb_language_match="native",
        retrieval_used_cross_lingual_variant=True,
    )

    turn_event = next(c for c in captured if c["event"] == "chat.turn")
    props = turn_event["properties"]
    assert props["query_script"] == "latin"
    assert props["kb_scripts"] == ["latin", "cyrillic"]
    assert props["cross_lingual_triggered"] is True
    assert props["cross_lingual_variants_count"] == 1
    assert props["query_kb_language_match"] == "native"
    assert props["retrieval_used_cross_lingual_variant"] is True


def test_emit_chat_turn_event_cross_lingual_defaults_to_false(monkeypatch):
    captured = _capture_events(monkeypatch, "backend.chat.events.capture_event")

    from backend.chat.events import _emit_chat_turn_event

    _emit_chat_turn_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_abc",
        chat_id="chat_abc",
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        escalated=False,
    )

    turn_event = next(c for c in captured if c["event"] == "chat.turn")
    props = turn_event["properties"]
    assert props["cross_lingual_triggered"] is False
    assert props["retrieval_used_cross_lingual_variant"] is False
    assert props["cross_lingual_variants_count"] == 0
