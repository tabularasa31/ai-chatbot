"""What the inbound lane has already acted on.

Brevo re-delivers the entire webhook body after any non-2xx, and one body can
carry replies to several tickets. Without a record of what was handled, a batch
where one reply was written into a chat and another failed to send offered only
two ways to be wrong: answer 200 and drop the failure, or answer 503 and write
the first reply into the conversation again on redelivery.

A receipt is written *after* the message has been dealt with, never before. A
crash in between leaves it unrecorded and the message is handled twice — a
duplicate, which somebody can see and undo, rather than a silently lost answer,
which nobody can.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import InboundEmailReceipt

logger = logging.getLogger(__name__)


def already_handled(provider_message_id: str, db: Session) -> bool:
    """Has this exact message been dealt with by an earlier delivery?

    A message with no provider id is always "not handled": we have nothing to
    deduplicate on, and refusing it to be safe would throw away an answer
    somebody wrote. A duplicate is the lesser harm, and the one we choose
    everywhere in this lane.
    """
    key = (provider_message_id or "").strip()
    if not key:
        return False
    return (
        db.query(InboundEmailReceipt.id)
        .filter(InboundEmailReceipt.provider_message_id == key)
        .first()
        is not None
    )


def record_handled(
    provider_message_id: str,
    *,
    ticket_id: uuid.UUID | None,
    outcome: str,
    db: Session,
) -> None:
    """Note that this message needs no further action. Commits.

    Committed on its own rather than left for the caller: the ingest that
    precedes it has already committed, so leaving the receipt uncommitted would
    reopen exactly the window it exists to close.

    A losing race writes the same key twice and the unique index rejects the
    second. That means another delivery recorded it first, which is the state
    this was trying to reach, so the conflict is rolled back and ignored.
    """
    key = (provider_message_id or "").strip()
    if not key:
        return
    try:
        db.add(
            InboundEmailReceipt(
                provider_message_id=key,
                ticket_id=ticket_id,
                outcome=outcome,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        # Never fail an answer that has already been delivered over the
        # bookkeeping that says so. The cost of losing this row is one possible
        # duplicate if Brevo re-delivers.
        db.rollback()
        logger.warning("email_lane_receipt_write_failed", exc_info=True)
