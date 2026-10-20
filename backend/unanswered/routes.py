"""HTTP routes for the Knowledge "Unanswered" queue.

Open to every verified member of the workspace, owner or operator: reading the
queue and triaging it (dismiss, restore) are not administrative acts.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.middleware import require_member
from backend.core.db import get_async_db
from backend.models import UnansweredGroupStatus, User
from backend.unanswered import service
from backend.unanswered.schemas import (
    UnansweredBadgeResponse,
    UnansweredDismissRequest,
    UnansweredGroupResponse,
    UnansweredListResponse,
)

MAX_PAGE_SIZE = 50

unanswered_router = APIRouter(prefix="/api/v1/knowledge/unanswered", tags=["knowledge"])

Member = Annotated[User, Depends(require_member)]
Db = Annotated[AsyncSession, Depends(get_async_db)]


@unanswered_router.get("", response_model=UnansweredListResponse)
async def list_unanswered(
    member: Member,
    db: Db,
    group_status: Annotated[UnansweredGroupStatus, Query(alias="status")] = (
        UnansweredGroupStatus.open
    ),
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = MAX_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UnansweredListResponse:
    return await service.list_groups(
        db, tenant_id=member.tenant_id, status=group_status, limit=limit, offset=offset
    )


@unanswered_router.get("/badge", response_model=UnansweredBadgeResponse)
async def unanswered_badge(member: Member, db: Db) -> UnansweredBadgeResponse:
    return await service.new_count(db, user=member)


@unanswered_router.post("/seen", status_code=status.HTTP_204_NO_CONTENT)
async def mark_unanswered_seen(member: Member, db: Db) -> Response:
    await service.mark_seen(db, user_id=member.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@unanswered_router.post("/{group_id}/dismiss", response_model=UnansweredGroupResponse)
async def dismiss_unanswered(
    group_id: uuid.UUID, payload: UnansweredDismissRequest, member: Member, db: Db
) -> UnansweredGroupResponse:
    try:
        return await service.dismiss(
            db, tenant_id=member.tenant_id, group_id=group_id, reason=payload.reason
        )
    except service.GroupNotFoundError:
        raise HTTPException(status_code=404, detail="Unanswered question not found") from None
    except service.GroupStateConflictError:
        raise HTTPException(
            status_code=409, detail="Only an open question can be dismissed"
        ) from None


@unanswered_router.post("/{group_id}/restore", response_model=UnansweredGroupResponse)
async def restore_unanswered(
    group_id: uuid.UUID, member: Member, db: Db
) -> UnansweredGroupResponse:
    try:
        return await service.restore(db, tenant_id=member.tenant_id, group_id=group_id)
    except service.GroupNotFoundError:
        raise HTTPException(status_code=404, detail="Unanswered question not found") from None
    except service.GroupStateConflictError:
        raise HTTPException(
            status_code=409, detail="Only a dismissed question can be restored"
        ) from None
