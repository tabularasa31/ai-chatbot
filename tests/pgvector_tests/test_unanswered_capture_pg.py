"""Grouping of captured unanswered questions against real pgvector.

The SQLite suite proves capture through the app with stub embeddings that are
all identical; only PostgreSQL can show that the cosine ranking picks the
nearest group and honours the similarity threshold.

Run: ``make pgvector-only`` (or ``pytest -m pgvector tests/pgvector_tests/``).
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy.orm import Session

from backend.unanswered.capture import UnansweredTurn, capture_unanswered_turn

pytestmark = pytest.mark.pgvector

_DIM = 1536


def _unit(x: float, y: float) -> list[float]:
    norm = math.hypot(x, y)
    return [x / norm, y / norm] + [0.0] * (_DIM - 2)


def _turn(db: Session, tenant_id: uuid.UUID, vector: list[float]) -> UnansweredTurn:
    from backend.models import Chat, Message, MessageRole

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db.add(chat)
    db.flush()
    user = Message(chat_id=chat.id, role=MessageRole.user, content="question")
    assistant = Message(chat_id=chat.id, role=MessageRole.assistant, content="No answer")
    db.add_all([user, assistant])
    db.commit()
    return UnansweredTurn(
        tenant_id=tenant_id,
        chat_id=chat.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        question_vector=vector,
        turn_outcome="unanswered",
        escalation_offer_reason=None,
    )


@pytest.mark.asyncio
async def test_question_joins_the_nearest_group_above_the_threshold(
    pg_db_session: Session, pg_async_db_session
) -> None:
    from backend.models import Tenant, UnansweredQuestion

    tenant = Tenant(name="grouping")
    pg_db_session.add(tenant)
    pg_db_session.commit()

    # (question, vector, expected group index). "gamma" is nearest to the
    # second group but below the threshold; "delta" clears the threshold for
    # both of the first two groups and must pick the nearer one.
    scenarios = [
        ("alpha", _unit(1.0, 0.0), 0),
        ("beta", _unit(0.6, 0.8), 1),
        ("gamma", _unit(0.0, 1.0), 2),
        ("delta", _unit(0.906, 0.423), 0),
        ("epsilon", _unit(0.62, 0.78), 1),
    ]
    group_ids: list[uuid.UUID] = []
    for question, vector, expected_index in scenarios:
        await capture_unanswered_turn(
            pg_async_db_session, _turn(pg_db_session, tenant.id, vector)
        )
        pg_db_session.expire_all()
        row = (
            pg_db_session.query(UnansweredQuestion)
            .order_by(UnansweredQuestion.created_at.desc())
            .first()
        )
        if expected_index == len(group_ids):
            group_ids.append(row.group_id)
        assert row.group_id == group_ids[expected_index], question

    assert len(group_ids) == 3
