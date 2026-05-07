"""Admin metrics endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from backend.admin.schemas import (
    AdminCacheCounter,
    AdminCacheStats,
    AdminMetricsSummary,
    AdminPiiEventItem,
    AdminPiiEventList,
    AdminTenantMetricsItem,
    AdminTenantMetricsList,
)
from backend.auth.middleware import require_admin_user, require_verified_user
from backend.core.db import get_db
from backend.models import (
    Chat,
    Document,
    Embedding,
    Message,
    MessageRole,
    PiiEvent,
    PiiEventDirection,
    Tenant,
    User,
)
from backend.observability.cache_metrics import snapshot as cache_snapshot
from backend.privacy_schemas import DeletedCountResponse

admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_admin_user(
    current_user: Annotated[User, Depends(require_verified_user)],
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
    total_tenants = db.query(Tenant).count()
    total_documents = db.query(Document).count()
    total_chat_sessions = db.query(Chat).count()
    total_messages_user = db.query(Message).filter(
        Message.role == MessageRole.user
    ).count()
    total_messages_assistant = db.query(Message).filter(
        Message.role == MessageRole.assistant
    ).count()
    total_tokens_chat = db.query(func.sum(Chat.tokens_used)).scalar() or 0

    doc_tenant_ids = {row[0] for row in db.query(Document.tenant_id).distinct()}
    chat_tenant_ids = {row[0] for row in db.query(Chat.tenant_id).distinct()}
    active_tenants = len(doc_tenant_ids & chat_tenant_ids)

    return AdminMetricsSummary(
        total_users=total_users,
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_documents=total_documents,
        total_chat_sessions=total_chat_sessions,
        total_messages_user=total_messages_user,
        total_messages_assistant=total_messages_assistant,
        total_tokens_chat=total_tokens_chat,
    )


@admin_router.get("/metrics/cache-stats", response_model=AdminCacheStats)
def get_cache_stats(
    _: Annotated[User, Depends(get_admin_user)],
) -> AdminCacheStats:
    """In-process hit/miss counters for the per-process caches.

    Counters are local to whichever app instance handles the request — for
    multi-worker deploys, snapshots vary between calls. Used to decide whether
    each cache is pulling its weight under real traffic.
    """
    return AdminCacheStats(
        caches={
            name: AdminCacheCounter(**counters)
            for name, counters in cache_snapshot().items()
        }
    )


@admin_router.get("/metrics/tenants", response_model=AdminTenantMetricsList)
def get_tenant_metrics(
    _: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminTenantMetricsList:
    """Per-tenant metrics table."""
    # NOTE: per-tenant counts (users/docs/chats/messages) are still N+1.
    # For current scale it's fine; replace with GROUP BY queries if tenant count grows.
    tenants = db.query(Tenant).all()

    # Pre-fetch one owner email per tenant in a single query to avoid N+1.
    owner_rows = (
        db.query(User.tenant_id, func.min(User.email))
        .filter(User.role == "owner", User.tenant_id.isnot(None))
        .group_by(User.tenant_id)
        .all()
    )
    owner_email_by_tenant: dict[uuid.UUID, str] = {row[0]: row[1] for row in owner_rows}

    items = []

    for c in tenants:
        users_count = db.query(User).filter(User.tenant_id == c.id).count()
        documents_count = db.query(Document).filter(Document.tenant_id == c.id).count()
        embedded_documents_count = (
            db.query(func.count(distinct(Document.id)))
            .filter(Document.tenant_id == c.id)
            .join(Embedding, Embedding.document_id == Document.id)
            .scalar()
        ) or 0
        chat_sessions_count = db.query(Chat).filter(Chat.tenant_id == c.id).count()
        messages_user_count = (
            db.query(Message)
            .join(Chat)
            .filter(Chat.tenant_id == c.id, Message.role == MessageRole.user)
            .count()
        )
        messages_assistant_count = (
            db.query(Message)
            .join(Chat)
            .filter(Chat.tenant_id == c.id, Message.role == MessageRole.assistant)
            .count()
        )
        tokens_used_chat = (
            db.query(func.sum(Chat.tokens_used))
            .filter(Chat.tenant_id == c.id)
            .scalar()
        ) or 0
        has_openai_key = bool(c.openai_api_key)

        items.append(
            AdminTenantMetricsItem(
                tenant_id=c.id,
                public_id=c.public_id,
                owner_email=owner_email_by_tenant.get(c.id),
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

    return AdminTenantMetricsList(items=items)


@admin_router.get("/privacy/pii-events", response_model=AdminPiiEventList)
def list_pii_events(
    _: Annotated[User, Depends(require_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=0, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    direction: str | None = None,
    tenant_id: str | None = None,
    actor_user_id: str | None = None,
    since_days: Annotated[int | None, Query(ge=1)] = None,
) -> AdminPiiEventList:
    q = db.query(PiiEvent).order_by(PiiEvent.created_at.desc())
    if direction:
        try:
            q = q.filter(PiiEvent.direction == PiiEventDirection(direction))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid direction") from exc
    if tenant_id:
        try:
            q = q.filter(PiiEvent.tenant_id == uuid.UUID(tenant_id))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid tenant_id") from exc
    if actor_user_id:
        try:
            q = q.filter(PiiEvent.actor_user_id == uuid.UUID(actor_user_id))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid actor_user_id") from exc
    if since_days is not None:
        since = datetime.now(UTC) - timedelta(days=since_days)
        q = q.filter(PiiEvent.created_at >= since)
    rows = q.offset(offset).limit(limit).all()
    return AdminPiiEventList(
        items=[
            AdminPiiEventItem(
                id=row.id,
                tenant_id=row.tenant_id,
                chat_id=row.chat_id,
                message_id=row.message_id,
                actor_user_id=row.actor_user_id,
                direction=row.direction,
                entity_type=row.entity_type,
                count=row.count,
                action_path=row.action_path,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@admin_router.delete("/privacy/pii-events/retention", response_model=DeletedCountResponse)
def cleanup_pii_events(
    _: Annotated[User, Depends(require_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    retention_days: Annotated[int, Query(ge=1)] = 365,
) -> DeletedCountResponse:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted_count = (
        db.query(PiiEvent)
        .filter(PiiEvent.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return DeletedCountResponse(deleted_count=int(deleted_count or 0))
