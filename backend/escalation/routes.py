"""JWT-protected escalation inbox API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.core.db import get_db
from backend.escalation.schemas import (
    EscalationListResponse,
    EscalationResolveRequest,
    EscalationTicketOut,
)
from backend.escalation.service import resolve_ticket
from backend.models import EscalationStatus, EscalationTicket, User
from backend.tenants.service import get_tenant_by_user

escalation_router = APIRouter(prefix="/escalations", tags=["escalations"])


def _serialize_ticket(ticket: EscalationTicket) -> EscalationTicketOut:
    """Ticket as stored — the tenant's own data, original wording included.

    Redaction happens where the ticket leaves the platform: the support email
    body and any OpenAI call built from the transcript.
    """
    return EscalationTicketOut(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        primary_question=ticket.primary_question,
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
) -> EscalationListResponse:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    q = db.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant.id)
    if status:
        try:
            st = EscalationStatus(status)
            q = q.filter(EscalationTicket.status == st)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid status") from None
    tickets = q.order_by(EscalationTicket.created_at.desc()).all()
    return EscalationListResponse(tickets=[_serialize_ticket(t) for t in tickets])


@escalation_router.get("/{ticket_id}", response_model=EscalationTicketOut)
def get_escalation(
    ticket_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EscalationTicketOut:
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    t = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.id == ticket_id, EscalationTicket.tenant_id == tenant.id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _serialize_ticket(t)


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
    return _serialize_ticket(t)
