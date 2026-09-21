"""Postgres-only branch of ``GET /analytics/summary``.

Verifies the dialect-aware "has source documents" predicate in
``backend.analytics.service._non_empty_source_documents``: a legacy
assistant row (``turn_outcome IS NULL``) with a non-empty
``source_documents`` array must count as answered on PostgreSQL, where
``func.cardinality`` is a real array function. On SQLite this branch is a
constant ``false`` (see ``tests/test_analytics_summary.py`` for the
SQLite-side behaviour, where such a row would NOT count as answered).

Run: pytest tests/pgvector_tests/test_analytics_summary_pg.py -m pgvector
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user


@pytest.mark.pgvector
def test_analytics_summary_legacy_row_with_source_documents_counts_as_answered(
    pg_client: TestClient, pg_db_session: Session
) -> None:
    from backend.models import Chat, Message, MessageRole
    from backend.models.base import _utcnow

    token = register_and_verify_user(pg_client, pg_db_session, email="pg-analytics@example.com")
    auth = {"Authorization": f"Bearer {token}"}
    resp = pg_client.post("/tenants", headers=auth, json={"name": "PG Analytics Co"})
    assert resp.status_code == 201, resp.text
    tenant_id = uuid.UUID(resp.json()["id"])

    now = _utcnow()
    recent = now - timedelta(hours=1)

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    pg_db_session.add(chat)
    pg_db_session.commit()
    pg_db_session.refresh(chat)

    pg_db_session.add(
        Message(chat_id=chat.id, role=MessageRole.user, content="hi", created_at=recent)
    )
    # Legacy row: no turn_outcome (predates the column), but it does have a
    # source document — the dialect-aware fallback must treat it as answered.
    pg_db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="here is the answer",
            turn_outcome=None,
            source_documents=[uuid.uuid4()],
            created_at=recent,
        )
    )
    pg_db_session.commit()

    resp = pg_client.get("/analytics/summary", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["messages"] == 1
    assert body["answered_rate"] == pytest.approx(1.0), (
        "legacy NULL turn_outcome + non-empty source_documents must count as "
        "answered on Postgres (cardinality() branch of "
        "_non_empty_source_documents)"
    )


@pytest.mark.pgvector
def test_analytics_summary_legacy_row_without_source_documents_not_answered(
    pg_client: TestClient, pg_db_session: Session
) -> None:
    """Control case: a legacy row with NO source documents must NOT count as
    answered, so the previous test is verifying the predicate and not just a
    denominator artifact."""
    from backend.models import Chat, Message, MessageRole
    from backend.models.base import _utcnow

    token = register_and_verify_user(pg_client, pg_db_session, email="pg-analytics-neg@example.com")
    auth = {"Authorization": f"Bearer {token}"}
    resp = pg_client.post("/tenants", headers=auth, json={"name": "PG Analytics Neg Co"})
    assert resp.status_code == 201, resp.text
    tenant_id = uuid.UUID(resp.json()["id"])

    now = _utcnow()
    recent = now - timedelta(hours=1)

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    pg_db_session.add(chat)
    pg_db_session.commit()
    pg_db_session.refresh(chat)

    pg_db_session.add(
        Message(chat_id=chat.id, role=MessageRole.user, content="hi", created_at=recent)
    )
    pg_db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="i don't know",
            turn_outcome=None,
            source_documents=None,
            created_at=recent,
        )
    )
    pg_db_session.commit()

    resp = pg_client.get("/analytics/summary", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["messages"] == 1
    assert body["answered_rate"] == pytest.approx(0.0)
