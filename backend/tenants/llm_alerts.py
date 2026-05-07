"""Tenant-level LLM-failure alerts.

When a chat turn hits an actionable LLM failure (the tenant's OpenAI key is
out of credits or invalid — failures the tenant can fix), we:

  1. Record the failure on the tenant row so the dashboard can surface a
     banner and any subsequent successful turn knows to clear it.
  2. Email the tenant admin once per 24h per failure type, so they actually
     learn about the problem without getting spammed every chat turn.

Transient OpenAI failures (timeouts, 5xx, ordinary rate-limits) do not
trigger this — they're not the tenant's problem to fix.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.chat.llm_unavailable import LlmFailureType
from backend.email.service import send_email
from backend.models import Tenant, User

logger = logging.getLogger(__name__)

# Failure types the tenant can act on. Other types (timeout, transient,
# rate-limit, unknown) are OpenAI's problem and resolve themselves; they
# get classified for the widget UX but don't raise a tenant-level alert.
_ACTIONABLE_TYPES: frozenset[str] = frozenset(
    {LlmFailureType.quota_exhausted.value, LlmFailureType.invalid_api_key.value}
)

EMAIL_THROTTLE = timedelta(hours=24)


def is_actionable(failure_type: str | LlmFailureType) -> bool:
    value = failure_type.value if isinstance(failure_type, LlmFailureType) else failure_type
    return value in _ACTIONABLE_TYPES


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _email_subject(failure_type: str) -> str:
    if failure_type == LlmFailureType.quota_exhausted.value:
        return "[Chat9] OpenAI quota exceeded — action required"
    if failure_type == LlmFailureType.invalid_api_key.value:
        return "[Chat9] OpenAI API key is invalid — action required"
    return "[Chat9] Chat is currently unavailable — action required"


def _email_body(failure_type: str) -> str:
    if failure_type == LlmFailureType.quota_exhausted.value:
        return (
            "Hello,\n\n"
            "Your OpenAI API key has run out of credits. Chat9 cannot generate "
            "responses for your users until you top up your balance.\n\n"
            "Please visit https://platform.openai.com/settings/organization/billing "
            "to add credits.\n\n"
            "— Chat9"
        )
    if failure_type == LlmFailureType.invalid_api_key.value:
        return (
            "Hello,\n\n"
            "Your OpenAI API key is invalid or has been revoked. Chat9 cannot "
            "generate responses for your users until you update the key.\n\n"
            "Please visit your Chat9 dashboard settings and paste a working "
            "OpenAI API key.\n\n"
            "— Chat9"
        )
    return (
        "Hello,\n\n"
        "Chat9 is currently unable to generate responses for your users. "
        "Please check your dashboard for details.\n\n"
        "— Chat9"
    )


def record_llm_failure(
    db: Session,
    tenant: Tenant,
    failure_type: str | LlmFailureType,
) -> None:
    """Mark the tenant as having an active LLM-failure alert and email the
    admin if outside the throttle window.

    Safe to call on every failing turn — throttling is enforced inside.
    No-ops for non-actionable failure types.
    """
    value = failure_type.value if isinstance(failure_type, LlmFailureType) else failure_type
    if value not in _ACTIONABLE_TYPES:
        return

    now = _now()
    type_changed = tenant.llm_alert_type != value
    if type_changed:
        tenant.llm_alert_type = value
        tenant.llm_alert_first_at = now

    last_email = tenant.llm_alert_last_email_at
    should_email = type_changed or last_email is None or (now - last_email) >= EMAIL_THROTTLE
    if should_email:
        _try_send_admin_email(db, tenant, value)
        tenant.llm_alert_last_email_at = now

    db.add(tenant)
    db.commit()


def clear_llm_alert(db: Session, tenant: Tenant) -> bool:
    """Clear the alert if one is set. Returns True iff state changed.

    Called on every successful chat turn — cheap when no alert is set.
    """
    if tenant.llm_alert_type is None:
        return False
    tenant.llm_alert_type = None
    tenant.llm_alert_first_at = None
    tenant.llm_alert_last_email_at = None
    db.add(tenant)
    db.commit()
    return True


def _try_send_admin_email(db: Session, tenant: Tenant, failure_type: str) -> None:
    # Target the tenant owner (= the first user attached to this tenant —
    # typically its creator). The User.is_admin column is Chat9's super-admin
    # flag (for /admin routes), not "tenant owner", so it cannot be used
    # here — most tenants have no super-admin in their member list.
    owner = (
        db.query(User)
        .filter(User.tenant_id == tenant.id)
        .order_by(User.created_at.asc())
        .first()
    )
    if owner is None:
        return
    try:
        send_email(
            to=owner.email,
            subject=_email_subject(failure_type),
            body=_email_body(failure_type),
        )
    except Exception:
        logger.warning(
            "llm_alert_email_failed",
            extra={"tenant_id": str(tenant.id), "failure_type": failure_type},
        )
