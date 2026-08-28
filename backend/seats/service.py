"""Operator seats: who may operate, and how that is asked.

A seat is what a customer buys. A role is what an owner assigns. They are
different axes and neither implies the other:

* ``role`` governs what you may **administer** — keys, members, the knowledge
  base, settings;
* a seat governs whether you may **operate** — the console, replies that land
  in the chat thread, and answers that feed the knowledge loop.

Without a seat, a person's reply still reaches the customer — it is mailed on
to them — but it never enters the transcript, so the chat thread does not learn
the question was answered and the knowledge loop gets nothing from it.

**Where a seat lives.** ``users.seat_granted_at`` — nullable, NULL for no
seat, a timestamp for the moment one was granted. The seat is an attribute of
the person because that is what is sold, and putting it on the row that
represents the person means the two questions below are a column read rather
than a join, and means a departing member cannot leave a dangling seat behind
(``remove_member`` deletes the row, so the seat goes with it).

**Who ends up seated.** Inviting somebody grants their seat in the same
action — a colleague is invited in order to answer customers, and a member
who cannot answer is not the thing anyone is asking for. Removing them
releases it. There is deliberately no third step in between, so an invited
member is always seated. The single account that can sit in a workspace
without a seat is that workspace's founding owner, who administers without
one and takes a seat only if they also want to answer from the console
themselves; that case is the whole reason the seat stays its own attribute
rather than being derived from membership.

**The two questions, asked at two different moments.** They are separate
functions rather than one with a flag because they are asked by different code
at different times and a wrong answer means a different failure:

* :func:`tenant_has_any_seat` — for the moment an escalation notification is
  composed: whether ``Reply-To`` carries our inbound token address or the
  visitor's own;
* :func:`user_holds_seat` — for the moment a reply arrives: whether it enters
  the chat thread.

**Both are called by the inbound e-mail lane** (``backend/email/``), which is
what they were cut for. ``tenant_has_any_seat`` decides which ``Reply-To`` an
escalation notification carries — our token address when somebody in the
workspace holds a seat, the visitor's own when nobody does. ``user_holds_seat``
decides what happens to a reply that arrives on it: a seat holder's enters the
chat thread, anybody else's is forwarded to the visitor by e-mail. Neither
question decides whether the customer is answered — only how.

The other place a seat is enforced is ``require_seated_member``, which gates
``/operator/*``: the console, where the reply also reaches the transcript.

**Known and accepted:** a shared support mailbox defeats per-seat accounting.
Five people answering from ``support@example.com`` all attribute to whoever
owns that address, and one seat covers them all. It cannot be fixed while
attribution is by sender address.

Both take keyword-only arguments named after the entity they are about, so
handing one the other's id does not silently type-check into a plausible
answer — ``tenant_has_any_seat(tenant_id=...)`` will not accept ``user_id=``.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.models import User
from backend.models.base import _utcnow


def holds_seat(user: User | None) -> bool:
    """The seat predicate over a row already in hand.

    Membership is part of it: ``users.tenant_id`` is nullable (the FK is
    ``ON DELETE SET NULL``), and a person whose workspace is gone holds no
    seat in it whatever their column says. Callers that already have the
    ``User`` — the auth dependency, which loaded it to authenticate the
    request — use this rather than paying for a second query.
    """
    return (
        user is not None
        and user.tenant_id is not None
        and user.seat_granted_at is not None
    )


def tenant_has_any_seat(*, tenant_id: uuid.UUID, db: Session) -> bool:
    """Does this **workspace** have at least one seated person?

    Asked when an escalation notification is composed
    (``backend.email.reply_lane.escalation_reply_to``): with a seat somewhere
    in the workspace the reply can come back through us, so ``Reply-To``
    carries our token address; with none it carries the visitor's own, or the
    answer lands nowhere. A workspace with no seat therefore sees no change at
    all from the lane existing.

    Counts only people still attached to this workspace, so a member removed
    (row deleted) or detached (``tenant_id`` nulled) stops counting the
    moment they go.
    """
    return (
        db.query(User.id)
        .filter(
            User.tenant_id == tenant_id,
            User.seat_granted_at.isnot(None),
        )
        .first()
        is not None
    )


def user_holds_seat(*, user_id: uuid.UUID, db: Session) -> bool:
    """Does this **person** hold a seat?

    Asked when a reply arrives on the inbound lane and its ``From`` has been
    matched to an account (``backend.email.inbound.handle_inbound_reply``): a
    seated author's answer enters the chat thread, an unseated one's is
    forwarded to the visitor by e-mail instead. Never a refusal either way. A
    user id that no longer exists answers ``False`` rather than raising — a
    deleted account is exactly a person who no longer holds a seat.
    """
    return holds_seat(db.query(User).filter(User.id == user_id).first())


def grant_seat(user: User) -> User:
    """Give this person a seat, if they do not already hold one.

    Idempotent, and deliberately does not re-stamp: the timestamp records
    when the seat was first taken, and a repeated grant is the same seat, not
    a new one. Staged only — the caller commits, so a grant that happens
    alongside another write (the invite) is atomic with it.
    """
    if user.seat_granted_at is None:
        user.seat_granted_at = _utcnow()
    return user


def release_seat(user: User) -> User:
    """Take this person's seat away. Staged only; the caller commits."""
    user.seat_granted_at = None
    return user


def count_seats(*, tenant_id: uuid.UUID, db: Session) -> int:
    """How many seats this workspace holds — what the screen prices."""
    return (
        db.query(User.id)
        .filter(
            User.tenant_id == tenant_id,
            User.seat_granted_at.isnot(None),
        )
        .count()
    )
