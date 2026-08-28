"""The inbound e-mail webhook — where an operator's mailed reply arrives.

``POST /email/inbound/<secret>``, fed by Brevo Inbound Parsing on the domain
whose MX points at them.

**Authentication is layered, because none of the layers is strong alone.**

1. *The path secret.* A shared secret in the URL, compared in constant time.
   Brevo publishes no signature over inbound webhook bodies and offers no
   place to configure one — the panel's inbound settings take a URL and
   nothing else — so there is no HMAC to verify here. If that changes, the
   check belongs in this function, next to the secret comparison. Without the
   secret configured the endpoint answers 404 to everybody, which is also
   what makes an unwired deployment behave as if the lane did not exist.
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
from backend.email.schemas import InboundEmailResponse

logger = logging.getLogger(__name__)

email_router = APIRouter(prefix="/email", tags=["email"])

#: Refusals all look alike on purpose.
_REFUSED = "Not found"


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
    onward send asks for a retry of the whole batch only when nothing in it
    landed. Brevo re-delivers on a non-2xx, so a 503 here must never be
    returned after something was written to a chat thread — that would write
    it twice.
    """
    _check_path_secret(secret)

    replies = parse_brevo_payload(payload)
    if not replies:
        return InboundEmailResponse(processed=0, outcomes=[])

    def _work(sync_db) -> list[str]:
        outcomes: list[str] = []
        for reply in replies:
            try:
                result = handle_inbound_reply(reply, sync_db)
                outcomes.append(result.outcome.value)
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
    if any(o == InboundOutcome.forward_failed.value for o in outcomes) and not any(
        o == InboundOutcome.ingested.value for o in outcomes
    ):
        # Nothing was written anywhere and an answer is still undelivered.
        # Asking Brevo to re-deliver cannot duplicate anything.
        raise HTTPException(status_code=503, detail="Delivery failed, retry")

    return InboundEmailResponse(processed=len(outcomes), outcomes=outcomes)
