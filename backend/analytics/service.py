"""Aggregation queries behind ``GET /analytics/summary``.

Everything here is computed with SQL aggregates over a single window —
no Python-side row loops, no persisted rollup.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import false as sql_false

from backend.analytics.schemas import AnalyticsPeriod, AnalyticsSummaryResponse
from backend.models import Chat, EscalationTicket, Message, MessageRole, TurnOutcome
from backend.models.base import _utcnow

PERIOD_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90}


def _non_empty_source_documents(db: AsyncSession) -> ColumnElement[bool]:
    """Portable "has at least one source document" predicate.

    Postgres arrays support ``cardinality()``. SQLite never receives a real
    array in this column at all — ``backend.chat.persistence._source_docs_for_db``
    deliberately writes ``NULL`` there instead of a Python list, because the
    sqlite3 DBAPI cannot bind one — so on that dialect this branch of the
    "answered" fallback can never be true and is compiled to a constant.
    """
    dialect_name = db.bind.dialect.name if db.bind is not None else None
    if dialect_name == "postgresql":
        return func.coalesce(func.cardinality(Message.source_documents), 0) > 0
    return sql_false()


async def compute_analytics_summary(
    db: AsyncSession, *, tenant_id: uuid.UUID, period: AnalyticsPeriod
) -> AnalyticsSummaryResponse:
    window_to = _utcnow()
    window_from = window_to - timedelta(days=PERIOD_DAYS[period])

    in_window = (
        Message.created_at >= window_from,
        Message.created_at <= window_to,
    )

    messages_stmt = (
        select(func.count(Message.id))
        .select_from(Message)
        .join(Chat, Chat.id == Message.chat_id)
        .where(
            Chat.tenant_id == tenant_id,
            Message.role == MessageRole.user,
            *in_window,
        )
    )
    messages_count = (await db.execute(messages_stmt)).scalar_one()

    conversations_stmt = (
        select(func.count(func.distinct(Chat.session_id)))
        .select_from(Chat)
        .join(Message, Message.chat_id == Chat.id)
        .where(Chat.tenant_id == tenant_id, *in_window)
    )
    conversations = (await db.execute(conversations_stmt)).scalar_one()

    escalated_sessions_stmt = select(
        func.count(func.distinct(EscalationTicket.session_id))
    ).where(
        EscalationTicket.tenant_id == tenant_id,
        EscalationTicket.created_at >= window_from,
        EscalationTicket.created_at <= window_to,
    )
    escalated_sessions = (await db.execute(escalated_sessions_stmt)).scalar_one()

    deflection_rate = (
        None if conversations == 0 else 1 - (escalated_sessions / conversations)
    )

    answered_condition = or_(
        Message.turn_outcome == TurnOutcome.answered.value,
        and_(
            Message.turn_outcome.is_(None),
            _non_empty_source_documents(db),
        ),
    )
    denominator_condition = Message.turn_outcome.is_distinct_from(
        TurnOutcome.filtered.value
    )
    filtered_condition = Message.turn_outcome == TurnOutcome.filtered.value

    assistant_stmt = (
        select(
            func.coalesce(func.sum(case((answered_condition, 1), else_=0)), 0),
            func.coalesce(func.sum(case((denominator_condition, 1), else_=0)), 0),
            func.coalesce(func.sum(case((filtered_condition, 1), else_=0)), 0),
        )
        .select_from(Message)
        .join(Chat, Chat.id == Message.chat_id)
        .where(
            Chat.tenant_id == tenant_id,
            Message.role == MessageRole.assistant,
            *in_window,
        )
    )
    answered_count, answered_denominator, filtered_count = (
        await db.execute(assistant_stmt)
    ).one()

    answered_rate = (
        None if answered_denominator == 0 else answered_count / answered_denominator
    )

    return AnalyticsSummaryResponse(
        period=period,
        from_=window_from,
        to=window_to,
        messages=messages_count,
        conversations=conversations,
        deflection_rate=deflection_rate,
        answered_rate=answered_rate,
        filtered=filtered_count,
    )
