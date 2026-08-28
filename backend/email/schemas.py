"""Schemas for the inbound e-mail webhook.

Only the *response* is modelled. The request body is Brevo's, not ours: its
shape varies by mail client, it is attacker-reachable, and a strict model would
turn an unexpected field into a 422 that silently drops a real operator's
answer. It is parsed defensively instead — see
:func:`backend.email.inbound.parse_brevo_payload`.
"""

from __future__ import annotations

from pydantic import BaseModel


class InboundEmailResponse(BaseModel):
    """What the webhook tells Brevo about each message it posted.

    ``outcomes`` is one entry per message in the batch, in order, so a batched
    delivery is debuggable from the response alone.
    """

    processed: int
    outcomes: list[str]
