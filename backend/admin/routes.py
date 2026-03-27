"""Admin metrics endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from backend.admin.schemas import (
    AdminClientMetricsItem,
    AdminClientMetricsList,
    AdminMetricsSummary,
    AdminPiiEventCleanupResponse,
    AdminPiiEventItem,
    AdminPiiEventList,
)
from backend.auth.middleware import get_current_user, require_admin_user
from backend.core.db import get_db
from backend.models import Chat, Client, Document, Embedding, Message, MessageRole, PiiEvent, PiiEventDirection, User

admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_admin_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require admin role. Raises 403 if not admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return current_user


@admin_router.get("/metrics/summary", response_model=AdminMetricsSummary)
def get_metrics_summary(
    _: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminMetricsSummary:
    """Platform-wide metrics summary."""
    total_users = db.query(User).count()
    total_clients = db.query(Client).count()
    total_documents = db.query(Document).count()
    total_chat_sessions = db.query(Chat).count()
    total_messages_user = db.query(Message).filter(
        Message.role == MessageRole.user
    ).count()
    total_messages_assistant = db.query(Message).filter(
        Message.role == MessageRole.assistant
    ).count()
    total_tokens_chat = db.query(func.sum(Chat.tokens_used)).scalar() or 0

    doc_client_ids = {row[0] for row in db.query(Document.client_id).distinct()}
    chat_client_ids = {row[0] for row in db.query(Chat.client_id).distinct()}
    active_clients = len(doc_client_ids & chat_client_ids)

    return AdminMetricsSummary(
        total_users=total_users,
        total_clients=total_clients,
        active_clients=active_clients,
        total_documents=total_documents,
        total_chat_sessions=total_chat_sessions,
        total_messages_user=total_messages_user,
        total_messages_assistant=total_messages_assistant,
        total_tokens_chat=total_tokens_chat,
    )


@admin_router.get("/metrics/clients", response_model=AdminClientMetricsList)
def get_client_metrics(
    _: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientMetricsList:
    """Per-client metrics table."""
    # NOTE: This is N+1 per client (users/docs/chats/messages). For current scale it's fine,
    # but if number of clients grows, we should replace this with aggregated GROUP BY queries.
    clients = db.query(Client).all()
    items = []

    for c in clients:
        users_count = db.query(User).filter(User.client_id == c.id).count()
        documents_count = db.query(Document).filter(Document.client_id == c.id).count()
        embedded_documents_count = (
            db.query(func.count(distinct(Document.id)))
            .filter(Document.client_id == c.id)
            .join(Embedding, Embedding.document_id == Document.id)
            .scalar()
        ) or 0
        chat_sessions_count = db.query(Chat).filter(Chat.client_id == c.id).count()
        messages_user_count = (
            db.query(Message)
            .join(Chat)
            .filter(Chat.client_id == c.id, Message.role == MessageRole.user)
            .count()
        )
        messages_assistant_count = (
            db.query(Message)
            .join(Chat)
            .filter(Chat.client_id == c.id, Message.role == MessageRole.assistant)
            .count()
        )
        tokens_used_chat = (
            db.query(func.sum(Chat.tokens_used))
            .filter(Chat.client_id == c.id)
            .scalar()
        ) or 0
        has_openai_key = bool(c.openai_api_key)

        items.append(
            AdminClientMetricsItem(
                client_id=c.id,
                public_id=c.public_id,
                owner_email=c.user.email if c.user else None,
                users_count=users_count,
                documents_count=documents_count,
                embedded_documents_count=embedded_documents_count,
                chat_sessions_count=chat_sessions_count,
                messages_user_count=messages_user_count,
                messages_assistant_count=messages_assistant_count,
                tokens_used_chat=tokens_used_chat,
                has_openai_key=has_openai_key,
            )
        )

    return AdminClientMetricsList(items=items)


@admin_router.get("/privacy/pii-events", response_model=AdminPiiEventList)
def list_pii_events(
    _: Annotated[User, Depends(require_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 100,
    offset: int = 0,
    direction: Optional[str] = None,
    client_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    since_days: Optional[int] = None,
) -> AdminPiiEventList:
    q = db.query(PiiEvent).order_by(PiiEvent.created_at.desc())
    if direction:
        try:
            q = q.filter(PiiEvent.direction == PiiEventDirection(direction))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid direction") from exc
    if client_id:
        try:
            q = q.filter(PiiEvent.client_id == uuid.UUID(client_id))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid client_id") from exc
    if actor_user_id:
        try:
            q = q.filter(PiiEvent.actor_user_id == uuid.UUID(actor_user_id))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid actor_user_id") from exc
    if since_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        q = q.filter(PiiEvent.created_at >= since)
    rows = q.offset(offset).limit(min(limit, 200)).all()
    return AdminPiiEventList(
        items=[
            AdminPiiEventItem(
                id=row.id,
                client_id=row.client_id,
                chat_id=row.chat_id,
                message_id=row.message_id,
                actor_user_id=row.actor_user_id,
                direction=row.direction.value,
                entity_type=row.entity_type,
                count=row.count,
                action_path=row.action_path,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@admin_router.delete("/privacy/pii-events/retention", response_model=AdminPiiEventCleanupResponse)
def cleanup_pii_events(
    _: Annotated[User, Depends(require_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    retention_days: int = 365,
) -> AdminPiiEventCleanupResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted_count = (
        db.query(PiiEvent)
        .filter(PiiEvent.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return AdminPiiEventCleanupResponse(deleted_count=int(deleted_count or 0))
