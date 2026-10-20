"""Read and triage of the Knowledge "Unanswered" queue.

Every query is scoped by ``tenant_id``; a group of another tenant is
indistinguishable from a missing one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Chat,
    Message,
    UnansweredDismissReason,
    UnansweredGroup,
    UnansweredGroupStatus,
    UnansweredQuestion,
    UnansweredReason,
    User,
)
from backend.models.base import _utcnow
from backend.unanswered.schemas import (
    UnansweredBadgeResponse,
    UnansweredGroupResponse,
    UnansweredListResponse,
)

WINDOW = timedelta(days=30)


class GroupNotFoundError(Exception):
    pass


class GroupStateConflictError(Exception):
    pass


def _visible_groups(
    tenant_id: uuid.UUID, status: UnansweredGroupStatus, since: datetime
) -> Select[tuple[UnansweredGroup]]:
    """Open groups age out of view after 30 quiet days unless they were just
    reopened or restored; answered and dismissed groups stay listed."""
    query = select(UnansweredGroup).where(
        UnansweredGroup.tenant_id == tenant_id, UnansweredGroup.status == status
    )
    if status == UnansweredGroupStatus.open:
        query = query.where(
            or_(UnansweredGroup.last_asked_at >= since, UnansweredGroup.status_changed_at >= since)
        )
    return query


async def list_groups(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: UnansweredGroupStatus,
    limit: int,
    offset: int,
) -> UnansweredListResponse:
    since = _utcnow() - WINDOW
    groups = (
        (
            await db.execute(
                _visible_groups(tenant_id, status, since)
                .order_by(UnansweredGroup.last_asked_at.desc(), UnansweredGroup.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    tenant_chats = select(Chat.id).where(Chat.tenant_id == tenant_id)
    conversations_30d = (
        await db.execute(
            select(func.count()).select_from(
                tenant_chats.where(Chat.created_at >= since).subquery()
            )
        )
    ).scalar_one()
    has_conversations = (await db.execute(select(exists(tenant_chats)))).scalar_one()
    return UnansweredListResponse(
        items=await _to_responses(db, groups[:limit], since),
        next_offset=offset + limit if len(groups) > limit else None,
        tenant_conversations_30d=conversations_30d,
        tenant_has_conversations=has_conversations,
    )


async def _to_responses(
    db: AsyncSession, groups: list[UnansweredGroup], since: datetime
) -> list[UnansweredGroupResponse]:
    if not groups:
        return []
    ids = [g.id for g in groups]

    recent_chat = case((UnansweredQuestion.created_at >= since, UnansweredQuestion.chat_id))
    conversations = dict(
        (
            await db.execute(
                select(UnansweredQuestion.group_id, func.count(func.distinct(recent_chat)))
                .where(UnansweredQuestion.group_id.in_(ids))
                .group_by(UnansweredQuestion.group_id)
            )
        ).all()
    )

    reasons: dict[uuid.UUID, set[UnansweredReason]] = {}
    for group_id, reason in await db.execute(
        select(UnansweredQuestion.group_id, UnansweredQuestion.reason)
        .where(UnansweredQuestion.group_id.in_(ids))
        .distinct()
    ):
        reasons.setdefault(group_id, set()).add(reason)

    newest_first = func.row_number().over(
        partition_by=UnansweredQuestion.group_id,
        order_by=(UnansweredQuestion.created_at.desc(), UnansweredQuestion.id),
    )
    ranked = (
        select(
            UnansweredQuestion.group_id.label("group_id"),
            UnansweredQuestion.chat_id.label("chat_id"),
            UnansweredQuestion.user_message_id.label("message_id"),
            newest_first.label("rank"),
        )
        .where(UnansweredQuestion.group_id.in_(ids))
        .subquery()
    )
    latest = {
        row.group_id: row
        for row in await db.execute(
            select(ranked.c.group_id, ranked.c.chat_id, ranked.c.message_id, Message.content)
            .join(Message, Message.id == ranked.c.message_id)
            .where(ranked.c.rank == 1)
        )
    }

    return [
        UnansweredGroupResponse(
            id=g.id,
            status=g.status,
            question=latest[g.id].content,
            question_message_id=latest[g.id].message_id,
            chat_id=latest[g.id].chat_id,
            conversations_30d=conversations[g.id],
            last_asked_at=g.last_asked_at,
            reasons=sorted(reasons[g.id], key=lambda r: r.value),
            reopened_after_answer=g.reopened_after_answer,
            faq_id=g.faq_id,
            has_draft=g.draft_answer is not None,
            dismiss_reason=g.dismiss_reason,
        )
        for g in groups
    ]


async def new_count(db: AsyncSession, *, user: User) -> UnansweredBadgeResponse:
    """Open groups that became open after the member last looked at the queue."""
    query = _visible_groups(user.tenant_id, UnansweredGroupStatus.open, _utcnow() - WINDOW)
    seen = user.knowledge_queue_seen_at
    if seen is not None:
        query = query.where(
            or_(UnansweredGroup.first_seen_at > seen, UnansweredGroup.status_changed_at > seen)
        )
    count = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    return UnansweredBadgeResponse(new_count=count)


async def mark_seen(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    await db.execute(
        update(User).where(User.id == user_id).values(knowledge_queue_seen_at=_utcnow())
    )
    await db.commit()


async def _get_group(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> UnansweredGroup:
    group = (
        await db.execute(
            select(UnansweredGroup).where(
                UnansweredGroup.id == group_id, UnansweredGroup.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if group is None:
        raise GroupNotFoundError
    return group


async def _change_status(
    db: AsyncSession,
    group: UnansweredGroup,
    *,
    status: UnansweredGroupStatus,
    dismiss_reason: UnansweredDismissReason | None,
) -> UnansweredGroupResponse:
    group.status = status
    group.dismiss_reason = dismiss_reason
    group.status_changed_at = _utcnow()
    await db.commit()
    (response,) = await _to_responses(db, [group], _utcnow() - WINDOW)
    return response


async def dismiss(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    reason: UnansweredDismissReason,
) -> UnansweredGroupResponse:
    """Raises ``GroupNotFoundError`` for a foreign group, ``GroupStateConflictError`` unless it is open."""
    group = await _get_group(db, tenant_id=tenant_id, group_id=group_id)
    if group.status != UnansweredGroupStatus.open:
        raise GroupStateConflictError
    return await _change_status(
        db, group, status=UnansweredGroupStatus.dismissed, dismiss_reason=reason
    )


async def restore(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> UnansweredGroupResponse:
    """Raises ``GroupNotFoundError`` for a foreign group, ``GroupStateConflictError`` unless it is dismissed."""
    group = await _get_group(db, tenant_id=tenant_id, group_id=group_id)
    if group.status != UnansweredGroupStatus.dismissed:
        raise GroupStateConflictError
    return await _change_status(db, group, status=UnansweredGroupStatus.open, dismiss_reason=None)
