"""JWT-protected escalation inbox API."""

from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_user
from backend.clients.service import get_client_by_user
from backend.core.db import get_db
from backend.escalation.schemas import (
    EscalationListResponse,
    EscalationResolveRequest,
    EscalationTicketOut,
)
from backend.escalation.service import resolve_ticket
from backend.models import EscalationStatus, EscalationTicket, User

escalation_router = APIRouter(prefix="/escalations", tags=["escalations"])


@escalation_router.get("", response_model=EscalationListResponse)
def list_escalations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[Optional[str], Query()] = None,
) -> EscalationListResponse:
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    q = db.query(EscalationTicket).filter(EscalationTicket.client_id == client.id)
    if status:
        try:
            st = EscalationStatus(status)
            q = q.filter(EscalationTicket.status == st)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid status")
    tickets = q.order_by(EscalationTicket.created_at.desc()).all()
    return EscalationListResponse(tickets=[EscalationTicketOut.model_validate(t) for t in tickets])


@escalation_router.get("/{ticket_id}", response_model=EscalationTicketOut)
def get_escalation(
    ticket_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EscalationTicketOut:
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    t = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.id == ticket_id, EscalationTicket.client_id == client.id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return EscalationTicketOut.model_validate(t)


@escalation_router.post("/{ticket_id}/resolve", response_model=EscalationTicketOut)
def resolve_escalation(
    ticket_id: uuid.UUID,
    body: EscalationResolveRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EscalationTicketOut:
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    try:
        t = resolve_ticket(ticket_id, client.id, body.resolution_text, db)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return EscalationTicketOut.model_validate(t)
