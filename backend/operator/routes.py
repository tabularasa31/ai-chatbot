"""Operator handoff HTTP API (phase 0 — no UI).

Three write routes: take a conversation, answer in it, hand it back to the
bot. Every one of them is tenant-scoped through
:func:`~backend.operator.service.get_tenant_chat`, so a chat belonging to
another tenant returns 404 rather than 403 — unreachable, not merely
forbidden.

Authorization is ``require_member`` — a verified user holding ``owner`` or
``operator`` in this workspace (phase 0.5) — plus tenant ownership of the
chat. Working the inbox is what an operator is for, so both roles pass; the
refusal this adds over phase 0 is for a principal who belongs to no workspace
at all, or holds a role that is not one of the two.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.middleware import require_member
from backend.core.db import get_async_db, run_sync
from backend.models import Chat, User
from backend.operator.schemas import (
    OperatorChatStateResponse,
    OperatorMessageRequest,
    OperatorMessageResponse,
)
from backend.operator.service import (
    OperatorActor,
    OperatorChannel,
    claim_chat,
    get_tenant_chat,
    ingest_from_operator,
    release_chat,
)
from backend.tenants.service import get_tenant_by_user

operator_router = APIRouter(prefix="/operator", tags=["operator"])


def _state(chat: Chat) -> OperatorChatStateResponse:
    return OperatorChatStateResponse(
        chat_id=chat.id,
        operator_state=chat.operator_state.value,
        assigned_operator_id=chat.assigned_operator_id,
        operator_joined_at=chat.operator_joined_at,
        operator_released_at=chat.operator_released_at,
    )


def _require_tenant_id(db, user: User) -> uuid.UUID:
    tenant = get_tenant_by_user(user.id, db)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.id


def _require_chat(db, *, chat_id: uuid.UUID, user: User) -> tuple[Chat, uuid.UUID]:
    tenant_id = _require_tenant_id(db, user)
    chat = get_tenant_chat(db, chat_id=chat_id, tenant_id=tenant_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat, tenant_id


@operator_router.post(
    "/chats/{chat_id}/take",
    response_model=OperatorChatStateResponse,
)
async def take_chat(
    chat_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> OperatorChatStateResponse:
    """Claim a conversation and mute the bot in it.

    409 when another operator already holds the chat. That is a lost race, not
    an error condition: the claim is a single conditional UPDATE, so exactly
    one of two simultaneous takes wins and the other is told so cleanly.
    """

    def _work(sync_db) -> OperatorChatStateResponse:
        chat, tenant_id = _require_chat(sync_db, chat_id=chat_id, user=current_user)
        if not claim_chat(
            sync_db, chat_id=chat.id, tenant_id=tenant_id, user_id=current_user.id
        ):
            raise HTTPException(
                status_code=409, detail="Chat is already taken by another operator"
            )
        # The claim was a bulk UPDATE with synchronize_session=False, so the
        # identity-mapped row still holds the pre-claim values.
        sync_db.refresh(chat)
        return _state(chat)

    return await run_sync(db, _work)


@operator_router.post(
    "/chats/{chat_id}/messages",
    response_model=OperatorMessageResponse,
)
async def send_operator_message(
    chat_id: uuid.UUID,
    body: OperatorMessageRequest,
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> OperatorMessageResponse:
    """Answer the visitor directly.

    Sets the chat ``live`` and claims it when unclaimed, so an operator who
    simply starts typing does not have to press "take" first. A chat already
    claimed by a colleague is answered without reassignment — assignment is
    advisory, and a shared support inbox has no single claimant.
    """

    def _work(sync_db) -> OperatorMessageResponse:
        chat, tenant_id = _require_chat(sync_db, chat_id=chat_id, user=current_user)
        result = ingest_from_operator(
            sync_db,
            chat=chat,
            tenant_id=tenant_id,
            text=body.text,
            actor=OperatorActor(
                channel=OperatorChannel.console, user_id=current_user.id
            ),
        )
        return OperatorMessageResponse(
            message_id=result.message.id,
            created_at=result.message.created_at,
            chat=_state(chat),
            chat_reopened=result.chat_reopened,
        )

    return await run_sync(db, _work)


@operator_router.post(
    "/chats/{chat_id}/release",
    response_model=OperatorChatStateResponse,
)
async def release_chat_route(
    chat_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> OperatorChatStateResponse:
    """Hand the conversation back to the bot.

    Idempotent: releasing a chat that is already in ``bot`` is a no-op that
    still returns the current state, so a double click or a retried request
    never fails.
    """

    def _work(sync_db) -> OperatorChatStateResponse:
        chat, _tenant_id = _require_chat(sync_db, chat_id=chat_id, user=current_user)
        return _state(release_chat(sync_db, chat))

    return await run_sync(db, _work)
