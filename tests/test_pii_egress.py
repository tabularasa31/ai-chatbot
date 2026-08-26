"""PII is stored raw and redacted at the boundaries where text leaves us.

These tests pin the contract the storage/egress split rests on:

* messages and escalation tickets keep the ORIGINAL wording;
* nothing raw reaches OpenAI — not the question, not the prompt history,
  not the escalation transcript, not the background log-analysis job;
* the support email keeps its EMAIL/IP exception and nothing else;
* the migration that flips storage back to originals never blanks a row.

If the egress redaction is ever removed, the OpenAI assertions here fail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.followup import build_dialog_context
from backend.chat.steps.generate import _build_prior_messages_for_llm
from backend.core.config import settings
from backend.core.crypto import encrypt_value
from backend.escalation.service import transcript_messages_for_openai
from backend.migrations.versions import pii_restore_originals_v1
from backend.migrations.versions.pii_restore_originals_v1 import (
    _guard_nothing_restored,
    _require_usable_key,
    _restore,
)
from backend.models import (
    Chat,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    Message,
    MessageRole,
    PiiEvent,
    PiiEventDirection,
)
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key

SECRET_EMAIL = "victor.raw@example.com"
QUESTION_WITH_PII = f"my invoice is broken, write me at {SECRET_EMAIL}"


def _openai_payload_text(mock_openai_client: Mock) -> str:
    """Everything this turn actually handed to OpenAI, as one blob."""
    parts: list[str] = []
    for call in mock_openai_client.chat.completions.create.call_args_list:
        for message in call.kwargs.get("messages") or []:
            if isinstance(message, dict):
                parts.append(str(message.get("content", "")))
    for call in mock_openai_client.embeddings.create.call_args_list:
        payload = call.kwargs.get("input")
        if isinstance(payload, str):
            parts.append(payload)
        elif isinstance(payload, list):
            parts.extend(str(item) for item in payload)
    return "\n".join(parts)


def _seed_tenant_with_kb(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="kb.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Invoices are issued monthly.",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="Invoices are issued monthly.",
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "Invoices are issued monthly.",
        total_tokens=42,
    )
    return tenant_id, api_key


# ── Storage keeps the original ────────────────────────────────────────────────

def test_chat_turn_stores_the_original_text(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    tenant_id, api_key = _seed_tenant_with_kb(
        mock_openai_client,
        tenant,
        db_session,
        email="egress-store@example.com",
        name="Egress Store Tenant",
    )

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": QUESTION_WITH_PII},
    )
    assert resp.status_code == 200

    session_id = uuid.UUID(resp.json()["session_id"])
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    user_message = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.user)
        .one()
    )
    # The row holds exactly what the user typed — no masked copy, no ciphertext.
    assert user_message.content == QUESTION_WITH_PII
    assert not hasattr(user_message, "content_redacted")
    assert not hasattr(user_message, "content_original_encrypted")


def test_chat_turn_records_an_egress_pii_event(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The privacy log still tells the tenant what was masked on the way out."""
    tenant_id, api_key = _seed_tenant_with_kb(
        mock_openai_client,
        tenant,
        db_session,
        email="egress-event@example.com",
        name="Egress Event Tenant",
    )

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": QUESTION_WITH_PII},
    )
    assert resp.status_code == 200

    events = (
        db_session.query(PiiEvent)
        .filter(PiiEvent.tenant_id == tenant_id)
        .all()
    )
    assert [e.entity_type for e in events] == ["EMAIL"]
    assert events[0].direction == PiiEventDirection.llm_request


# ── Nothing raw reaches OpenAI ────────────────────────────────────────────────

def test_question_sent_to_openai_is_redacted(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The published promise: masked before being sent to OpenAI.

    Fails loudly if the pipeline is ever handed the raw question again.
    """
    _tenant_id, api_key = _seed_tenant_with_kb(
        mock_openai_client,
        tenant,
        db_session,
        email="egress-question@example.com",
        name="Egress Question Tenant",
    )

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": QUESTION_WITH_PII},
    )
    assert resp.status_code == 200

    sent = _openai_payload_text(mock_openai_client)
    assert SECRET_EMAIL not in sent
    assert "[EMAIL]" in sent


def test_prompt_history_sent_to_openai_is_redacted(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Turn 2 must not smuggle turn 1's raw text back out via the history."""
    _tenant_id, api_key = _seed_tenant_with_kb(
        mock_openai_client,
        tenant,
        db_session,
        email="egress-history@example.com",
        name="Egress History Tenant",
    )

    first = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": QUESTION_WITH_PII},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    mock_openai_client.chat.completions.create.reset_mock()
    mock_openai_client.embeddings.create.reset_mock()

    second = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "any update on that?", "session_id": session_id},
    )
    assert second.status_code == 200

    sent = _openai_payload_text(mock_openai_client)
    assert SECRET_EMAIL not in sent


def test_build_prior_messages_masks_stored_originals() -> None:
    chat = Chat(id=uuid.uuid4(), tenant_id=uuid.uuid4(), session_id=uuid.uuid4())
    base = datetime(2026, 1, 1, 12, 0, 0)
    chat.messages = [
        Message(
            id=uuid.uuid4(),
            chat_id=chat.id,
            role=MessageRole.user,
            content=QUESTION_WITH_PII,
            created_at=base,
        ),
        Message(
            id=uuid.uuid4(),
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="Call 8-999-123-45-67 for billing.",
            created_at=base + timedelta(minutes=1),
        ),
    ]

    prior = _build_prior_messages_for_llm(chat, max_messages=10, char_cap=500)

    assert prior is not None
    rendered = "\n".join(m["content"] for m in prior)
    assert SECRET_EMAIL not in rendered
    assert "[EMAIL]" in rendered
    assert "[PHONE]" in rendered


def test_dialog_context_masks_stored_originals() -> None:
    chat_id = uuid.uuid4()
    messages = [
        Message(
            id=uuid.uuid4(),
            chat_id=chat_id,
            role=MessageRole.user,
            content=QUESTION_WITH_PII,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
    ]

    block = build_dialog_context(messages)

    assert block is not None
    assert SECRET_EMAIL not in block
    assert "[EMAIL]" in block


def test_escalation_transcript_for_openai_masks_stored_originals() -> None:
    chat = Chat(id=uuid.uuid4(), tenant_id=uuid.uuid4(), session_id=uuid.uuid4())
    chat.messages = [
        Message(
            id=uuid.uuid4(),
            chat_id=chat.id,
            role=MessageRole.user,
            content=QUESTION_WITH_PII,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
    ]

    msgs = transcript_messages_for_openai(chat)

    assert SECRET_EMAIL not in msgs[0]["content"]
    assert "[EMAIL]" in msgs[0]["content"]


def test_log_analysis_job_masks_messages_before_embedding(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The Phase 4 job embeds stored turns — that is an OpenAI call too."""
    from backend.jobs.analyze_chat_logs import _load_messages

    token = register_and_verify_user(tenant, db_session, email="egress-job@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Egress Job Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content=QUESTION_WITH_PII,
            # Stored naive-UTC, like the chat pipeline writes them.
            created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5),
        )
    )
    db_session.commit()

    rows = _load_messages(db_session, tenant_id, None, 50)

    assert rows
    assert SECRET_EMAIL not in rows[0].content
    assert "[EMAIL]" in rows[0].content


# ── Migration backfill ────────────────────────────────────────────────────────

def _legacy_messages_engine() -> sa.Engine:
    """Standalone SQLite DB shaped like the pre-migration ``messages`` table."""
    legacy_engine = sa.create_engine("sqlite://", future=True)
    with legacy_engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE messages ("
                "id TEXT PRIMARY KEY, "
                "content TEXT NOT NULL, "
                "content_original_encrypted TEXT)"
            )
        )
    return legacy_engine


def test_backfill_restores_content_from_the_encrypted_original() -> None:
    with _legacy_messages_engine().begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO messages (id, content, content_original_encrypted) "
                "VALUES (:id, :content, :enc)"
            ),
            {
                "id": "m1",
                "content": "my invoice is broken, write me at [EMAIL]",
                "enc": encrypt_value(QUESTION_WITH_PII),
            },
        )

        restored, skipped = _restore(
            connection, "messages", "content", "content_original_encrypted"
        )

        assert (restored, skipped) == (1, 0)
        stored = connection.execute(
            sa.text("SELECT content FROM messages WHERE id = 'm1'")
        ).scalar_one()
        assert stored == QUESTION_WITH_PII


def test_backfill_leaves_rows_without_an_original_untouched() -> None:
    """No encrypted original = the masked text is all there is. Never blank it."""
    with _legacy_messages_engine().begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO messages (id, content, content_original_encrypted) "
                "VALUES ('legacy', 'contact me at [EMAIL]', NULL), "
                "('empty', 'no pii here', '')"
            )
        )

        restored, skipped = _restore(
            connection, "messages", "content", "content_original_encrypted"
        )

        assert (restored, skipped) == (0, 0)
        rows = dict(
            connection.execute(sa.text("SELECT id, content FROM messages")).all()
        )
        assert rows == {
            "legacy": "contact me at [EMAIL]",
            "empty": "no pii here",
        }


def test_backfill_skips_rows_whose_ciphertext_cannot_be_decrypted() -> None:
    with _legacy_messages_engine().begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO messages (id, content, content_original_encrypted) "
                "VALUES ('broken', 'contact me at [EMAIL]', 'not-a-fernet-token')"
            )
        )

        restored, skipped = _restore(
            connection, "messages", "content", "content_original_encrypted"
        )

        assert (restored, skipped) == (0, 1)
        stored = connection.execute(
            sa.text("SELECT content FROM messages WHERE id = 'broken'")
        ).scalar_one()
        assert stored == "contact me at [EMAIL]"


def test_backfill_pages_through_more_rows_than_one_batch(monkeypatch) -> None:
    """The read is streamed in pages; every row must still be restored exactly once."""
    monkeypatch.setattr(pii_restore_originals_v1, "_BATCH", 2)
    originals = {f"m{i}": f"reach me at user{i}@example.com" for i in range(5)}
    with _legacy_messages_engine().begin() as connection:
        for row_id, original in originals.items():
            connection.execute(
                sa.text(
                    "INSERT INTO messages (id, content, content_original_encrypted) "
                    "VALUES (:id, :content, :enc)"
                ),
                {"id": row_id, "content": "reach me at [EMAIL]", "enc": encrypt_value(original)},
            )

        restored, skipped = _restore(
            connection, "messages", "content", "content_original_encrypted"
        )

        assert (restored, skipped) == (5, 0)
        stored = dict(
            connection.execute(sa.text("SELECT id, content FROM messages")).all()
        )
        assert stored == originals


def test_backfill_refuses_to_run_without_a_usable_key(monkeypatch) -> None:
    """An absent key would skip every row, then the next revision drops them."""
    monkeypatch.setattr(settings, "encryption_key", None)

    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        _require_usable_key()


def test_backfill_aborts_when_a_usable_key_decrypted_nothing() -> None:
    """A rotated key: rows existed, none came back. Stop before the columns go."""
    with pytest.raises(RuntimeError, match="rotated"):
        _guard_nothing_restored("messages", restored=0, skipped=3)

    # Partial success and an empty table are both fine — nothing to protect.
    _guard_nothing_restored("messages", restored=1, skipped=2)
    _guard_nothing_restored("messages", restored=0, skipped=0)
