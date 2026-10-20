"""Capture of bot turns that left the visitor without an answer.

A turn is captured when the reply counted as unanswered, or when it offered a
handoff because the bot could not answer (``CAPTURED_OFFER_REASONS``). Whether
the visitor accepted the offer is irrelevant: the knowledge gap exists either
way. Explicit requests for a human and complaints are not knowledge gaps.

Captured questions are clustered by the cosine similarity of their embeddings.
The vector is the one the pipeline embedded for this very question, read back
from the in-process embedding cache, so capture never calls the embeddings API;
a turn whose vector is gone from the cache is captured into a group of its own.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import is_sqlite
from backend.models import (
    EscalationTrigger,
    MessageEmbedding,
    TurnOutcome,
    UnansweredGroup,
    UnansweredGroupStatus,
    UnansweredQuestion,
    UnansweredReason,
)
from backend.models.base import _utcnow
from backend.search import embedding_cache
from backend.search.query_variants import expand_query
from backend.utils.math import coerce_vector, cosine_similarity

logger = logging.getLogger(__name__)

CAPTURED_OFFER_REASONS = frozenset(
    {
        EscalationTrigger.llm_self_offer.value,
        EscalationTrigger.low_similarity.value,
        EscalationTrigger.no_documents.value,
    }
)
GROUPING_SIMILARITY = 0.85
DISMISSED_REOPEN_CHATS = 5
ANSWERED_REOPEN_CHATS = 2
ANSWERED_REOPEN_WINDOW = timedelta(days=14)


@dataclass(frozen=True)
class UnansweredTurn:
    """A persisted bot turn, as far as capture needs to know it."""

    tenant_id: uuid.UUID
    chat_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    question: str
    turn_outcome: str | None
    escalation_offer_reason: str | None


def capture_reason(turn: UnansweredTurn) -> UnansweredReason | None:
    """Why the turn counts as unanswered, or ``None`` when it must not be captured."""
    if turn.escalation_offer_reason in CAPTURED_OFFER_REASONS:
        return UnansweredReason(turn.escalation_offer_reason)
    if turn.turn_outcome == TurnOutcome.unanswered.value:
        return UnansweredReason.unanswered
    return None


async def capture_unanswered_turn(db: AsyncSession, turn: UnansweredTurn) -> None:
    """Record the turn in the unanswered queue; never raises.

    Runs after the turn's own rows are committed, so a failure here is logged
    and rolled back without touching the visitor's conversation.
    """
    reason = capture_reason(turn)
    if reason is None:
        return
    try:
        await _capture(db, turn, reason)
    except Exception:
        logger.exception(
            "unanswered_capture_failed: tenant_id=%s assistant_message_id=%s",
            turn.tenant_id,
            turn.assistant_message_id,
        )
        try:
            await db.rollback()
        except Exception:
            logger.warning("unanswered_capture_rollback_failed", exc_info=True)


async def _capture(db: AsyncSession, turn: UnansweredTurn, reason: UnansweredReason) -> None:
    already_captured = await db.scalar(
        select(UnansweredQuestion.id).where(
            UnansweredQuestion.assistant_message_id == turn.assistant_message_id
        )
    )
    if already_captured is not None:
        return

    now = _utcnow()
    vector = embedding_cache.get(expand_query(turn.question)[0])
    group = await _nearest_group(db, turn.tenant_id, vector) if vector else None
    if group is None:
        group = UnansweredGroup(
            tenant_id=turn.tenant_id,
            first_seen_at=now,
            last_asked_at=now,
            status_changed_at=now,
        )
        db.add(group)
    else:
        group.last_asked_at = now
    if vector:
        await db.merge(
            MessageEmbedding(
                message_id=turn.user_message_id, tenant_id=turn.tenant_id, embedding=vector
            )
        )
    await db.flush()

    db.add(
        UnansweredQuestion(
            tenant_id=turn.tenant_id,
            group_id=group.id,
            chat_id=turn.chat_id,
            user_message_id=turn.user_message_id,
            assistant_message_id=turn.assistant_message_id,
            reason=reason,
            created_at=now,
        )
    )
    await db.flush()
    await _reopen_if_due(db, group, now)
    await db.commit()


async def _nearest_group(
    db: AsyncSession, tenant_id: uuid.UUID, vector: list[float]
) -> UnansweredGroup | None:
    """The tenant's group (any status) holding the question closest to ``vector``."""
    in_tenant = (
        select(UnansweredQuestion.group_id)
        .join(MessageEmbedding, MessageEmbedding.message_id == UnansweredQuestion.user_message_id)
        .where(UnansweredQuestion.tenant_id == tenant_id)
    )
    best_group_id: uuid.UUID | None = None
    best_similarity = 0.0
    if is_sqlite(db):
        rows = (await db.execute(in_tenant.add_columns(MessageEmbedding.embedding))).all()
        for group_id, raw in rows:
            stored = coerce_vector(raw)
            similarity = cosine_similarity(vector, stored) if stored is not None else 0.0
            if similarity > best_similarity:
                best_group_id, best_similarity = group_id, similarity
    else:
        distance = MessageEmbedding.embedding.cosine_distance(vector)
        row = (
            await db.execute(
                in_tenant.add_columns(distance.label("distance")).order_by(distance).limit(1)
            )
        ).first()
        if row is not None:
            best_group_id, best_similarity = row[0], 1.0 - float(row[1])
    if best_group_id is None or best_similarity < GROUPING_SIMILARITY:
        return None
    return await db.get(UnansweredGroup, best_group_id)


async def _distinct_chats_after(
    db: AsyncSession,
    group: UnansweredGroup,
    *,
    after: datetime,
    until: datetime | None = None,
) -> int:
    conditions = [
        UnansweredQuestion.group_id == group.id,
        UnansweredQuestion.created_at > after,
    ]
    if until is not None:
        conditions.append(UnansweredQuestion.created_at <= until)
    return int(
        await db.scalar(
            select(func.count(func.distinct(UnansweredQuestion.chat_id))).where(*conditions)
        )
        or 0
    )


async def _reopen_if_due(db: AsyncSession, group: UnansweredGroup, now: datetime) -> None:
    """A dismissed group reopens once enough new chats ask again; an answered
    one reopens when the published answer did not stop the questions."""
    if group.status == UnansweredGroupStatus.dismissed:
        chats = await _distinct_chats_after(db, group, after=group.status_changed_at)
        if chats < DISMISSED_REOPEN_CHATS:
            return
        group.dismiss_reason = None
    elif group.status == UnansweredGroupStatus.answered:
        chats = await _distinct_chats_after(
            db,
            group,
            after=group.status_changed_at,
            until=group.status_changed_at + ANSWERED_REOPEN_WINDOW,
        )
        if chats < ANSWERED_REOPEN_CHATS:
            return
        group.reopened_after_answer = True
    else:
        return
    group.status = UnansweredGroupStatus.open
    group.status_changed_at = now
