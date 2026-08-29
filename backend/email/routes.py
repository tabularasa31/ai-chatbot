"""The inbound e-mail webhook — where an operator's mailed reply arrives.

``POST /email/inbound/<secret>``, fed by Brevo Inbound Parsing on the domain
whose MX points at them.

**Authentication is layered, because none of the layers is strong alone.**

1. *The path secret.* A shared secret in the URL, compared in constant time.
   Brevo's inbound-parsing documentation describes the payload and no
   authentication at all: no signature header, no HMAC, and — unlike their
   *event* webhooks, which take custom headers on create — nothing to
   configure on the inbound side beyond the URL itself. A secret in the path
   is therefore the whole transport-level check. Whoever wires the panel
   should look again for a signing option and add its verification here, next
   to this comparison, if one has appeared. Without the secret configured the
   endpoint answers 404 to everybody, which is also what makes an unwired
   deployment behave as if the lane did not exist.
2. *The per-ticket token* in the recipient address. Knowing the URL is not
   enough to write into a conversation; you need an address only somebody
   holding the notification has seen.
3. *The ``In-Reply-To`` cross-check*, recorded but never enforced — see
   :func:`backend.email.inbound._threading_matches` for why gating on it
   would lose real answers.

Every refusal is a 404. Distinguishing "wrong secret" from "no such lane" from
"unknown token" would tell a prober which half of the URL they had right.

The endpoint is deliberately not rate-limited by IP: the callers are Brevo's
delivery nodes, and limiting them throttles a support team's replies rather
than an attacker's, who is bounded by having to guess a 43-character token per
conversation.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.db import get_async_db, run_sync
from backend.email.inbound import (
    InboundOutcome,
    UnknownReplyTokenError,
    handle_inbound_reply,
    parse_brevo_payload,
)
from backend.email.receipts import already_handled, record_handled
from backend.email.schemas import InboundEmailResponse

logger = logging.getLogger(__name__)

email_router = APIRouter(prefix="/email", tags=["email"])

#: Refusals all look alike on purpose.
_REFUSED = "Not found"

#: Reported for a message a previous delivery already dealt with.
_ALREADY_HANDLED = "already_handled"


def _check_path_secret(secret: str) -> None:
    configured = settings.inbound_email_secret
    if not configured or not secrets.compare_digest(secret, configured):
        raise HTTPException(status_code=404, detail=_REFUSED)


@email_router.post(
    "/inbound/{secret}",
    response_model=InboundEmailResponse,
    include_in_schema=False,
)
async def inbound_email(
    secret: str,
    payload: dict | list = Body(default_factory=dict),
    db: AsyncSession = Depends(get_async_db),
) -> InboundEmailResponse:
    """Take Brevo's delivery of one or more replies and act on each.

    A batch is processed message by message and each message's outcome is
    independent: one unknown token does not discard the batch, and one failed
    onward send asks for the batch again without disturbing its neighbours.
    Brevo re-delivers the whole body on a non-2xx, and what makes that safe is
    the receipt written for every message that was dealt with — a redelivery
    skips those and re-attempts only what actually failed.
    """
    _check_path_secret(secret)

    replies = parse_brevo_payload(payload)
    if not replies:
        return InboundEmailResponse(processed=0, outcomes=[])

    def _work(sync_db) -> list[str]:
        outcomes: list[str] = []
        for reply in replies:
            if already_handled(reply.provider_message_id, sync_db):
                # A redelivery of something this lane already acted on. Brevo
                # re-sends the whole body, so a batch retried for one failed
                # message carries its successful neighbours along with it.
                outcomes.append(_ALREADY_HANDLED)
                continue
            try:
                result = handle_inbound_reply(reply, sync_db)
                outcomes.append(result.outcome.value)
                if result.outcome is not InboundOutcome.forward_failed:
                    record_handled(
                        reply.provider_message_id,
                        ticket_id=result.ticket_id,
                        outcome=result.outcome.value,
                        db=sync_db,
                    )
            except UnknownReplyTokenError:
                # Logged without the sender's address: an inbound body is
                # untrusted content and its From is somebody's personal data.
                logger.info("email_lane_unknown_token")
                outcomes.append("unknown_token")
        return outcomes

    outcomes = await run_sync(db, _work)

    if outcomes and all(o == "unknown_token" for o in outcomes):
        # Every message in the batch addressed nothing. Refused, and refused
        # the same way as a bad secret.
        raise HTTPException(status_code=404, detail=_REFUSED)
    if any(o == InboundOutcome.forward_failed.value for o in outcomes):
        # An answer is still undelivered. Asking for the batch again is safe:
        # everything that succeeded left a receipt and will be skipped, so the
        # redelivery re-attempts only what failed. Before receipts this branch
        # had to also require that nothing was ingested, which meant a batch
        # mixing a written reply with a failed send returned 200 and dropped
        # the failure on the floor.
        raise HTTPException(status_code=503, detail="Delivery failed, retry")

    return InboundEmailResponse(processed=len(outcomes), outcomes=outcomes)
