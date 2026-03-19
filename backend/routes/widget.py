"""Widget API routes for embedded chat (public, clientId-based)."""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import APIError
from sqlalchemy.orm import Session

from backend.chat.service import process_chat_message
from backend.core.db import get_db
from backend.models import Client

widget_router = APIRouter(prefix="/widget", tags=["widget"])


@widget_router.get("/health")
def widget_health() -> dict[str, str]:
    """Health check for widget endpoints."""
    return {"status": "ok"}


@widget_router.post("/chat")
def widget_chat(
    message: Annotated[str, Query(description="User message")],
    client_id: Annotated[str, Query(description="Public client ID (ch_xyz)")],
    session_id: Annotated[Optional[str], Query(description="Optional session ID")] = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    PUBLIC endpoint for embedded widget.
    No authentication required (clientId = permission).
    """
    client = db.query(Client).filter(Client.public_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if not client.is_active:
        raise HTTPException(status_code=403, detail="Client is not active")

    if not client.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    try:
        sid = uuid.UUID(session_id) if session_id else uuid.uuid4()
    except (ValueError, TypeError):
        sid = uuid.uuid4()

    try:
        answer, _document_ids, _tokens_used = process_chat_message(
            client_id=client.id,
            question=message,
            session_id=sid,
            db=db,
            api_key=client.openai_api_key,
        )
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        )

    return {
        "response": answer,
        "session_id": str(sid),
    }
