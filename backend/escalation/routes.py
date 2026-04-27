"""JWT-protected escalation inbox API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.middleware import require_admin_user, require_verified_user
from backend.core.crypto import decrypt_value
from backend.core.db import get_db
from backend.escalation.schemas import (
    EscalationListResponse,
    EscalationResolveRequest,
    EscalationTicketOut,
)
from backend.escalation.service import delete_ticket_original_content, resolve_ticket
from backend.models import EscalationStatus, EscalationTicket, PiiEvent, PiiEventDirection, User
from backend.privacy_schemas import DeletedCountResponse
from backend.tenants.service import get_tenant_by_user

escalation_router = APIRouter(prefix="/escalations", tags=["escalations"])


def _require_original_access(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Original content access requires admin privileges")


def _serialize_ticket(ticket: EscalationTicket, *, include_original: bool) -> EscalationTicketOut:
    original = None
    if include_original and ticket.primary_question_original_encrypted:
        try:
            original = decrypt_value(ticket.primary_question_original_encrypted)
        except RuntimeError:
            original = None
    return EscalationTicketOut(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        primary_question=ticket.primary_question_redacted or ticket.primary_question,
        primary_question_original=original,
        primary_question_original_available=bool(ticket.primary_question_original_encrypted),
        conversation_summary=ticket.conversation_summary,
        trigger=ticket.trigger.value,
        best_similarity_score=ticket.best_similarity_score,
        retrieved_chunks_preview=ticket.retrieved_chunks_preview,
        user_id=ticket.user_id,
        user_email=ticket.user_email,
        user_name=ticket.user_name,
        plan_tier=ticket.plan_tier,
        user_note=ticket.user_note,
        priority=ticket.priority.value,
        status=ticket.status.value,
        resolution_text=ticket.resolution_text,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        chat_id=ticket.chat_id,
        session_id=ticket.session_id,
    )


@escalation_router.get("", response_model=EscalationListResponse)
def list_escalations(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
    include_original: bool = Query(False),
) -> EscalationListResponse:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if include_original:
        _require_original_access(current_user)

    q = db.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant.id)
    if status:
        try:
            st = EscalationStatus(status)
            q = q.filter(EscalationTicket.status == st)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid status") from None
    tickets = q.order_by(EscalationTicket.created_at.desc()).all()
    if include_original:
        for ticket in tickets:
            if not ticket.primary_question_original_encrypted:
                continue
            db.add(
                PiiEvent(
                    tenant_id=tenant.id,
                    chat_id=ticket.chat_id,
                    message_id=None,
                    actor_user_id=current_user.id,
                    direction=PiiEventDirection.original_view,
                    entity_type="ORIGINAL_VIEW",
                    count=1,
                    action_path="/escalations",
                )
            )
        db.commit()
    return EscalationListResponse(
        tickets=[_serialize_ticket(t, include_original=include_original) for t in tickets]
    )


@escalation_router.get("/{ticket_id}", response_model=EscalationTicketOut)
def get_escalation(
    ticket_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    include_original: bool = Query(False),
) -> EscalationTicketOut:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if include_original:
        _require_original_access(current_user)
    t = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.id == ticket_id, EscalationTicket.tenant_id == tenant.id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if include_original and t.primary_question_original_encrypted:
        db.add(
            PiiEvent(
                tenant_id=tenant.id,
                chat_id=t.chat_id,
                message_id=None,
                actor_user_id=current_user.id,
                direction=PiiEventDirection.original_view,
                entity_type="ORIGINAL_VIEW",
                count=1,
                action_path=f"/escalations/{ticket_id}",
            )
        )
        db.commit()
    return _serialize_ticket(t, include_original=include_original)


@escalation_router.post("/{ticket_id}/resolve", response_model=EscalationTicketOut)
def resolve_escalation(
    ticket_id: uuid.UUID,
    body: EscalationResolveRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EscalationTicketOut:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        t = resolve_ticket(ticket_id, tenant.id, body.resolution_text, db)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ticket not found") from None
    return _serialize_ticket(t, include_original=False)


@escalation_router.post("/{ticket_id}/delete-original", response_model=DeletedCountResponse, include_in_schema=False)
def delete_escalation_original(
    ticket_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeletedCountResponse:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    ticket, deleted_count = delete_ticket_original_content(ticket_id, tenant.id, db)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if deleted_count:
        db.add(
            PiiEvent(
                tenant_id=tenant.id,
                chat_id=ticket.chat_id,
                message_id=None,
                actor_user_id=current_user.id,
                direction=PiiEventDirection.original_delete,
                entity_type="ORIGINAL_DELETE",
                count=deleted_count,
                action_path=f"/escalations/{ticket_id}/delete-original",
            )
        )
        db.commit()
        db.refresh(ticket)
    return DeletedCountResponse(deleted_count=deleted_count)
