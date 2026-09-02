"""Operator console HTTP API.

Reads — the queue, its counts, one visitor's thread — are open to every
verified member of the workspace, either role, seat or not: a workspace
member may always see what is going on. Writes — take, answer, release,
resolve — are ``require_seated_member``: writing a human reply into the
visitor's transcript is precisely what a seat buys, so an owner without one
keeps every administrative surface and loses only this.

Every route is tenant-scoped through the lookup itself
(:func:`~backend.operator.service.get_tenant_chat`,
:func:`~backend.operator.inbox.load_thread`), so a chat or session belonging
to another tenant returns 404 rather than 403 — unreachable, not merely
forbidden.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.middleware import require_member, require_seated_member
from backend.core.db import get_async_db, run_sync
from backend.models import Chat, EscalationTicket, User
from backend.operator.inbox import (
    InboxRow,
    InboxScope,
    Thread,
    inbox_counts,
    list_inbox,
    load_thread,
)
from backend.operator.schemas import (
    InboxListResponse,
    InboxRowResponse,
    InboxSummaryResponse,
    InboxTicket,
    OperatorChatStateResponse,
    OperatorMessageRequest,
    OperatorMessageResponse,
    OperatorResolveRequest,
    OperatorResolveResponse,
    ThreadMessageResponse,
    ThreadResponse,
)
from backend.operator.service import (
    OperatorActor,
    OperatorChannel,
    claim_chat,
    get_tenant_chat,
    ingest_from_operator,
    release_chat,
    resolve_from_operator,
)
from backend.tenants.service import get_tenant_by_user

operator_router = APIRouter(prefix="/operator", tags=["operator"])


def _state(chat: Chat, *, assigned_operator_email: str | None = None) -> OperatorChatStateResponse:
    return OperatorChatStateResponse(
        chat_id=chat.id,
        operator_state=chat.operator_state.value,
        assigned_operator_id=chat.assigned_operator_id,
        assigned_operator_email=assigned_operator_email,
        operator_joined_at=chat.operator_joined_at,
        operator_released_at=chat.operator_released_at,
    )


def _ticket(ticket: EscalationTicket | None) -> InboxTicket | None:
    if ticket is None:
        return None
    return InboxTicket(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        status=ticket.status.value,
        priority=ticket.priority.value,
        trigger=ticket.trigger.value,
        user_note=ticket.user_note,
        resolution_text=ticket.resolution_text,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
    )


def _row(row: InboxRow) -> InboxRowResponse:
    return InboxRowResponse(
        session_id=row.session_id,
        chat_id=row.chat_id,
        handoff_state=row.handoff_state,
        ticket=_ticket(row.ticket),
        assigned_operator_id=row.assigned_operator_id,
        assigned_operator_email=row.assigned_operator_email,
        waiting_since=row.waiting_since,
        last_message_role=row.last_message_role,
        last_message_preview=row.last_message_preview,
        last_activity=row.last_activity,
        message_count=row.message_count,
        visitor_email=row.visitor.email,
        visitor_name=row.visitor.name,
    )


def _thread(thread: Thread) -> ThreadResponse:
    return ThreadResponse(
        session_id=thread.session_id,
        chat=_state(thread.chat, assigned_operator_email=thread.assigned_operator_email),
        handoff_state=thread.handoff_state,
        chat_ended=thread.chat.ended_at is not None,
        ticket=_ticket(thread.ticket),
        visitor_email=thread.visitor.email,
        visitor_name=thread.visitor.name,
        messages=[
            ThreadMessageResponse(
                id=m.message.id,
                chat_id=m.message.chat_id,
                role=m.message.role.value,
                content=m.message.content,
                created_at=m.message.created_at,
                author_label=m.author_label,
            )
            for m in thread.messages
        ],
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


@operator_router.get("/inbox", response_model=InboxListResponse)
async def inbox(
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
    scope: Annotated[InboxScope, Query()] = "attention",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> InboxListResponse:
    """The queue. ``attention`` is what needs a human; ``all`` is everything.

    ``limit`` caps ``all`` only: the attention set is bounded by nature.
    """

    def _work(sync_db) -> InboxListResponse:
        tenant_id = _require_tenant_id(sync_db, current_user)
        rows = list_inbox(sync_db, tenant_id=tenant_id, scope=scope, limit=limit)
        counts = inbox_counts(sync_db, tenant_id=tenant_id)
        return InboxListResponse(
            items=[_row(r) for r in rows],
            waiting_count=counts.waiting,
            attention_count=counts.attention,
        )

    return await run_sync(db, _work)


@operator_router.get("/inbox/summary", response_model=InboxSummaryResponse)
async def inbox_summary(
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> InboxSummaryResponse:
    """Just the counts, for the sidebar badge."""

    def _work(sync_db) -> InboxSummaryResponse:
        counts = inbox_counts(sync_db, tenant_id=_require_tenant_id(sync_db, current_user))
        return InboxSummaryResponse(
            waiting_count=counts.waiting, attention_count=counts.attention
        )

    return await run_sync(db, _work)


@operator_router.get("/sessions/{session_id}", response_model=ThreadResponse)
async def session_thread(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> ThreadResponse:
    """One visitor's whole session, as stored.

    Originals are shown as written — redaction happens where text leaves the
    platform, not on the tenant reading back their own conversations.
    """

    def _work(sync_db) -> ThreadResponse:
        tenant_id = _require_tenant_id(sync_db, current_user)
        thread = load_thread(sync_db, tenant_id=tenant_id, session_id=session_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return _thread(thread)

    return await run_sync(db, _work)


@operator_router.post(
    "/chats/{chat_id}/take",
    response_model=OperatorChatStateResponse,
)
async def take_chat(
    chat_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_seated_member)],
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
        return _state(chat, assigned_operator_email=current_user.email)

    return await run_sync(db, _work)


@operator_router.post(
    "/chats/{chat_id}/messages",
    response_model=OperatorMessageResponse,
)
async def send_operator_message(
    chat_id: uuid.UUID,
    body: OperatorMessageRequest,
    current_user: Annotated[User, Depends(require_seated_member)],
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
    current_user: Annotated[User, Depends(require_seated_member)],
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


@operator_router.post(
    "/chats/{chat_id}/resolve",
    response_model=OperatorResolveResponse,
)
async def resolve_chat_route(
    chat_id: uuid.UUID,
    body: OperatorResolveRequest,
    current_user: Annotated[User, Depends(require_seated_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> OperatorResolveResponse:
    """Mark the conversation dealt with: close its tickets, hand it back.

    Idempotent like release: a chat with nothing active and nobody live is
    left as it is and the current state is returned.
    """

    def _work(sync_db) -> OperatorResolveResponse:
        chat, tenant_id = _require_chat(sync_db, chat_id=chat_id, user=current_user)
        result = resolve_from_operator(
            sync_db,
            chat=chat,
            tenant_id=tenant_id,
            resolution_text=body.resolution_text,
        )
        return OperatorResolveResponse(
            chat=_state(result.chat),
            resolved_ticket_numbers=[t.ticket_number for t in result.resolved_tickets],
        )

    return await run_sync(db, _work)
