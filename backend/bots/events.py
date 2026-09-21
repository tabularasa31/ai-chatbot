"""Analytics for bot settings saves.

Which fields a tenant changes when they save widget/bot settings, never what
they changed them to — the values can carry instructions, domains, or other
tenant content that has no place in product analytics.
"""

from __future__ import annotations

import logging

from backend.bots.schemas import BotUpdate
from backend.models import Bot
from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)

BOT_SETTINGS_UPDATED = "bot.settings_updated"


def changed_fields(bot: Bot, update: BotUpdate) -> list[str]:
    """Fields present in the payload whose value differs from the stored one.

    Compared before ``update_bot`` writes the new values onto ``bot``.
    """
    payload = update.model_dump(exclude_unset=True)
    changed = [field for field, value in payload.items() if value != getattr(bot, field)]
    return sorted(changed)


def emit_bot_settings_updated(bot: Bot, tenant_public_id: str, changed: list[str]) -> None:
    """Report a bot settings save. Call only after the commit that made it true."""
    if not changed:
        return
    try:
        capture_event(
            BOT_SETTINGS_UPDATED,
            distinct_id=str(bot.public_id),
            tenant_id=tenant_public_id,
            bot_id=str(bot.public_id),
            properties={
                "changed_fields": changed,
                "changed_count": len(changed),
            },
            groups={"tenant": tenant_public_id},
        )
    except Exception:
        logger.warning("Failed to emit %s event", BOT_SETTINGS_UPDATED, exc_info=True)
