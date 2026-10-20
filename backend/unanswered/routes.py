"""HTTP routes for the Knowledge "Unanswered" queue.

Open to every verified member of the workspace, owner or operator: reading the
queue, triaging it and working on a draft are not administrative acts.
Publishing writes to the knowledge base and is owner-only.

Draft generation and refinement run detached from the request: the member may
close the drawer while the model works and the draft is still stored.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.middleware import require_member, require_owner
from backend.core.db import get_async_db
from backend.core.limiter import limiter, owner_jwt_rate_limit_key
from backend.models import UnansweredGroupStatus, User
from backend.unanswered import drafts, service
from backend.unanswered.schemas import (
    DraftRefineRequest,
    DraftResponse,
    DraftSaveRequest,
    PublishRequest,
    PublishResponse,
    UnansweredBadgeResponse,
    UnansweredDismissRequest,
    UnansweredGroupResponse,
    UnansweredListResponse,
)

MAX_PAGE_SIZE = 50
DRAFT_MODEL_RATE_LIMIT = "10/minute"

unanswered_router = APIRouter(prefix="/api/v1/knowledge/unanswered", tags=["knowledge"])

Member = Annotated[User, Depends(require_member)]
Owner = Annotated[User, Depends(require_owner)]
Db = Annotated[AsyncSession, Depends(get_async_db)]

_STATUS_AND_CODE: dict[type[Exception], tuple[int, str]] = {
    service.GroupNotFoundError: (404, "group_not_found"),
    service.GroupStateConflictError: (409, "group_answered"),
    drafts.NoDraftError: (404, "no_draft"),
    drafts.NoOpenAIKeyError: (400, "no_openai_key"),
    drafts.DraftGeneratingError: (409, "draft_generating"),
    drafts.DraftGenerationError: (502, "draft_generation_failed"),
    drafts.DraftRejectedError: (422, "draft_rejected"),
    drafts.FaqEmbeddingError: (502, "faq_embedding_failed"),
}

_background_tasks: set[asyncio.Task] = set()


@contextmanager
def _draft_http_errors() -> Iterator[None]:
    try:
        yield
    except drafts.DraftVersionConflictError as exc:
        current = exc.current.model_dump() if exc.current is not None else None
        raise HTTPException(
            status_code=409, detail={"code": "stale_version", "draft": current}
        ) from None
    except tuple(_STATUS_AND_CODE) as exc:
        status_code, code = _STATUS_AND_CODE[type(exc)]
        raise HTTPException(status_code=status_code, detail={"code": code}) from None


async def _complete_without_client(work: Awaitable[DraftResponse]) -> DraftResponse:
    """Run ``work`` to completion even if the client disconnects and cancels the request."""
    task = asyncio.ensure_future(work)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return await asyncio.shield(task)


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
            status_code=409, detail="An answered question cannot be dismissed"
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
            status_code=409, detail="An answered question cannot be restored"
        ) from None


@unanswered_router.get("/{group_id}/draft", response_model=DraftResponse)
async def get_unanswered_draft(group_id: uuid.UUID, member: Member, db: Db) -> DraftResponse:
    with _draft_http_errors():
        return await drafts.get_draft(db, tenant_id=member.tenant_id, group_id=group_id)


@unanswered_router.post("/{group_id}/draft", response_model=DraftResponse)
@limiter.limit(DRAFT_MODEL_RATE_LIMIT, key_func=owner_jwt_rate_limit_key)
async def generate_unanswered_draft(
    request: Request, group_id: uuid.UUID, member: Member
) -> DraftResponse:
    with _draft_http_errors():
        return await _complete_without_client(
            drafts.generate_draft(tenant_id=member.tenant_id, group_id=group_id)
        )


@unanswered_router.put("/{group_id}/draft", response_model=DraftResponse)
async def save_unanswered_draft(
    group_id: uuid.UUID, payload: DraftSaveRequest, member: Member, db: Db
) -> DraftResponse:
    with _draft_http_errors():
        return await drafts.save_draft(
            db,
            tenant_id=member.tenant_id,
            group_id=group_id,
            content=drafts.DraftContent(question=payload.question, answer=payload.answer),
            version=payload.version,
        )


@unanswered_router.post("/{group_id}/draft/refine", response_model=DraftResponse)
@limiter.limit(DRAFT_MODEL_RATE_LIMIT, key_func=owner_jwt_rate_limit_key)
async def refine_unanswered_draft(
    request: Request, group_id: uuid.UUID, payload: DraftRefineRequest, member: Member
) -> DraftResponse:
    with _draft_http_errors():
        return await _complete_without_client(
            drafts.refine_draft(
                tenant_id=member.tenant_id,
                group_id=group_id,
                instructions=payload.instructions,
                version=payload.version,
            )
        )


@unanswered_router.post("/{group_id}/publish", response_model=PublishResponse)
async def publish_unanswered_draft(
    group_id: uuid.UUID, payload: PublishRequest, owner: Owner, db: Db
) -> PublishResponse:
    with _draft_http_errors():
        return await drafts.publish_draft(
            db, tenant_id=owner.tenant_id, group_id=group_id, version=payload.version
        )
