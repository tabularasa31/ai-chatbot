"""Single source of truth: when a Tenant (by public_id) may use public widget chat / escalate."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Tenant


class WidgetChatTenantGateError(Exception):
    """Public widget cannot proceed for this public_id; map to HTTP or domain errors upstream."""

    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    NO_OPENAI = "no_openai"

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def get_tenant_eligible_for_widget_chat(db: Session, public_id: str) -> Tenant:
    """
    Same eligibility as POST /widget/chat and POST /widget/escalate.

    Raises:
        WidgetChatTenantGateError: NOT_FOUND | INACTIVE | NO_OPENAI
    """
    tenant = db.query(Tenant).filter(Tenant.public_id == public_id).first()
    if not tenant:
        raise WidgetChatTenantGateError(WidgetChatTenantGateError.NOT_FOUND)
    if not tenant.is_active:
        raise WidgetChatTenantGateError(WidgetChatTenantGateError.INACTIVE)
    key = tenant.openai_api_key
    if not key or not str(key).strip():
        raise WidgetChatTenantGateError(WidgetChatTenantGateError.NO_OPENAI)
    return tenant
