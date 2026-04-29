"""Single source of truth: when a Bot/Tenant may use public widget chat / escalate."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Bot, Tenant


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
    Resolve a Tenant directly by its public_id. Used by eval service.

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


def _resolve_active_bot_and_tenant(db: Session, bot_public_id: str) -> tuple[Bot, Tenant]:
    """Lookup + NOT_FOUND/INACTIVE checks shared by every gate flavour."""
    result = (
        db.query(Bot, Tenant)
        .join(Tenant, Bot.tenant_id == Tenant.id)
        .filter(Bot.public_id == bot_public_id)
        .first()
    )
    if not result or not result[0].is_active:
        raise WidgetChatTenantGateError(WidgetChatTenantGateError.NOT_FOUND)
    bot, tenant = result
    if not tenant.is_active:
        raise WidgetChatTenantGateError(WidgetChatTenantGateError.INACTIVE)
    return bot, tenant


def get_bot_and_tenant_for_widget_chat(db: Session, bot_public_id: str) -> tuple[Bot, Tenant]:
    """
    Resolve a Bot by its public_id, then verify the owning Tenant is eligible.
    Used by POST /widget/chat and POST /widget/escalate.

    Raises:
        WidgetChatTenantGateError: NOT_FOUND | INACTIVE | NO_OPENAI
    """
    bot, tenant = _resolve_active_bot_and_tenant(db, bot_public_id)
    key = tenant.openai_api_key
    if not key or not str(key).strip():
        raise WidgetChatTenantGateError(WidgetChatTenantGateError.NO_OPENAI)
    return bot, tenant


def get_bot_and_tenant_for_widget_session(db: Session, bot_public_id: str) -> tuple[Bot, Tenant]:
    """
    Same lookup as get_bot_and_tenant_for_widget_chat but does not require an
    OpenAI key on the tenant. The session/init endpoint must be allowed to mint
    a session even when the key is not yet configured — the actual chat turn
    surfaces the NO_OPENAI error.

    Raises:
        WidgetChatTenantGateError: NOT_FOUND | INACTIVE
    """
    return _resolve_active_bot_and_tenant(db, bot_public_id)
