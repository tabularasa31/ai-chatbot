"""Analytics for human curation of the FAQ list.

One event, ``faq.reviewed``, covers every way a tenant touches a FAQ
candidate by hand: approve, reject, edit, or bulk-approve. It exists to tell
which tenants curate their FAQ at all, not to describe what they wrote —
question and answer text never appear in the properties.

Values that a later write would destroy (a rejected/deleted row's ``source``)
are read into :class:`FaqReviewed` before the commit that removes them, same
discipline as ``backend.seats.events``. The emit itself runs after the commit
and swallows every error: telemetry must never turn a completed review action
into a failed request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)

FAQ_REVIEWED = "faq.reviewed"

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_UPDATE = "update"
ACTION_APPROVE_ALL = "approve_all"


@dataclass(frozen=True)
class FaqReviewed:
    """One review action, resolved before the commit that recorded it."""

    tenant_public_id: str
    action: str
    count: int = 1
    content_changed: bool | None = None
    faq_source: str | None = None


def emit_faq_reviewed(change: FaqReviewed) -> None:
    """Report one FAQ review action. Call only after the commit."""
    properties: dict[str, object] = {"action": change.action, "count": change.count}
    if change.content_changed is not None:
        properties["content_changed"] = change.content_changed
    if change.faq_source is not None:
        properties["faq_source"] = change.faq_source
    try:
        capture_event(
            FAQ_REVIEWED,
            distinct_id=change.tenant_public_id,
            tenant_id=change.tenant_public_id,
            properties=properties,
            groups={"tenant": change.tenant_public_id},
        )
    except Exception:
        logger.warning("Failed to emit %s event", FAQ_REVIEWED, exc_info=True)
