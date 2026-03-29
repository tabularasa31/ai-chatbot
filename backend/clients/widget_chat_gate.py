"""Single source of truth: when a Client (by public_id) may use public widget chat / escalate."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Client


class WidgetChatClientGateError(Exception):
    """Public widget cannot proceed for this public_id; map to HTTP or domain errors upstream."""

    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    NO_OPENAI = "no_openai"

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def get_client_eligible_for_widget_chat(db: Session, public_client_id: str) -> Client:
    """
    Same eligibility as POST /widget/chat and POST /widget/escalate.

    Raises:
        WidgetChatClientGateError: NOT_FOUND | INACTIVE | NO_OPENAI
    """
    client = db.query(Client).filter(Client.public_id == public_client_id).first()
    if not client:
        raise WidgetChatClientGateError(WidgetChatClientGateError.NOT_FOUND)
    if not client.is_active:
        raise WidgetChatClientGateError(WidgetChatClientGateError.INACTIVE)
    key = client.openai_api_key
    if not key or not str(key).strip():
        raise WidgetChatClientGateError(WidgetChatClientGateError.NO_OPENAI)
    return client
