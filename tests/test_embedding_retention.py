"""Embedding retention purge, driven through the ARQ cron tick."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from backend.core.config import settings
from backend.jobs import embedding_retention
from backend.models import MessageEmbedding, Tenant


@pytest.mark.asyncio
async def test_retention_tick_purges_only_embeddings_past_the_window(
    db_session, engine, monkeypatch
):
    monkeypatch.setattr("backend.core.db.SessionLocal", sessionmaker(bind=engine))
    tenant = Tenant(name="Retention Tenant", public_id="retention-public-id")
    db_session.add(tenant)
    db_session.commit()

    now = datetime.now(UTC)
    window = timedelta(days=settings.log_embeddings_retention_days)
    stale_id, fresh_id = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            MessageEmbedding(
                message_id=message_id,
                tenant_id=tenant.id,
                embedding=[0.0] * 1536,
                last_used_at=last_used_at,
            )
            for message_id, last_used_at in (
                (stale_id, now - window - timedelta(days=1)),
                (fresh_id, now - window + timedelta(days=1)),
            )
        ]
    )
    db_session.commit()

    await embedding_retention._tick_message_embedding_retention({})

    db_session.expire_all()
    remaining = {row.message_id for row in db_session.query(MessageEmbedding).all()}
    assert remaining == {fresh_id}
