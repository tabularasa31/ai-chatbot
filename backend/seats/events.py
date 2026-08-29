"""Analytics for the seat lifecycle: a seat taken, and a seat handed back.

Seats are the entitlement customers buy, so the two questions the model lives
or dies by are whether they are being taken at all, and whether the people
holding them ever answer anything. ``tenants.plan`` used to carry the first
half of that story through ``tenant.plan.changed``; the column went with the
plan model and nothing replaced it, which left both questions unanswerable.
Hence these two events.

**Both are emitted after the commit that changed the seat, from a payload
read before it.** Same shape, and for the same reasons, as
``operator.sessions.ClosedStretch``:

* reading before means the facts are still there to read. Two of them are
  destroyed by the very write being described — ``seat_granted_at`` is nulled
  by a release, and the ``messages`` rows that answer "did they ever reply"
  have their ``operator_user_id`` set to NULL when a removed member's account
  is deleted. After the commit, ``answered`` would be ``False`` for every
  departing member, which is precisely the churn signal these events exist to
  carry;
* emitting after means the seat change is already durable. The emit touches
  no database and swallows everything, so telemetry cannot turn a completed
  removal into a 500 for the owner who asked for it.

**A change that did not happen reports nothing.** Both grants and releases
are idempotent at the service layer — taking a seat you already hold keeps
the date you took it, giving up a seat you do not hold is a no-op — and a
re-run of either would otherwise emit an event describing nothing. The
builders below return ``None`` in that case, and :func:`emit_seat_change`
accepts ``None`` so callers hand on whatever they were given without
branching.

**No money on either event.** A seat is displayed at $10 a month and nothing
is charged, so any amount here would be invented — and invented figures get
quoted. The seat count is on both events; whoever prices them can multiply.

**Where the seat count comes from.** ``seats`` is the count *after* the
change, computed as "everybody else's seats, plus one if this is a grant" —
so it does not depend on whether the caller has staged their write yet, and
cannot go stale between the two orderings the call sites actually use.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models import Message, MessageRole, User
from backend.models.base import _utcnow
from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)

SEAT_GRANTED = "seat_granted"
SEAT_RELEASED = "seat_released"

#: The owner gave their own seat back from the console.
RELEASE_GIVEN_UP = "given_up"
#: The owner removed a member, which deletes the account and the seat with it.
#: The common way a seat comes back — catching only the button above would
#: produce analytics in which seats are taken and almost never returned.
RELEASE_MEMBER_REMOVED = "member_removed"
#: The whole workspace was deleted, taking every member and every seat.
RELEASE_WORKSPACE_DELETED = "workspace_deleted"

_MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class SeatChange:
    """One seat event, fully resolved before the transaction that caused it.

    Frozen and self-contained: the emit runs after that commit, when the ORM
    objects this came from are expired or, in the removal case, gone.
    """

    event: str
    tenant_public_id: str
    user_id: str
    #: ``owner`` when the seat is the workspace founder's own, ``member`` when
    #: it belongs to somebody they invited. A founder who sits down to answer
    #: customers is a different customer profile from one who hires support
    #: and never opens the console, and they churn differently.
    holder: str
    #: Seats the workspace holds once this change lands.
    seats: int
    #: How long the seat was held, in milliseconds and in whole days. Release
    #: only. Days is the unit the churn question is asked in; milliseconds is
    #: what the repo's other duration properties use, and keeps a seat held
    #: for an afternoon from reporting a flat zero.
    held_ms: int | None = None
    held_days: int | None = None
    #: Did this holder ever produce an operator reply while holding *this*
    #: seat? Release only. The leading indicator: a seat that never produced
    #: one is a seat about to be given back.
    answered: bool | None = None
    #: Which way the seat came back — one of the ``RELEASE_*`` constants.
    #: Release only.
    reason: str | None = None


def _tenant_public_id(user: User) -> str | None:
    """The public id of the workspace this seat belongs to, or ``None``.

    ``None`` when the person belongs to no workspace: ``users.tenant_id`` is
    nullable, and a seat outside a workspace is not a seat anybody is paying
    for.

    It is emphatically **not** what keeps the founding-owner scrub in
    ``tenants.service.create_tenant`` quiet. That scrub runs *after*
    ``user.tenant_id`` has been pointed at the new workspace, so this would
    happily hand back the new workspace's id and the event would claim it
    released a seat it never sold. The only thing keeping it quiet is that
    nobody wrote the call — see :func:`capture_seat_released`, and
    ``tests/test_seat_analytics.py`` for the test that fails if somebody adds
    one for symmetry.
    """
    tenant = getattr(user, "tenant", None)
    public_id = getattr(tenant, "public_id", None)
    return str(public_id) if public_id else None


def _holder(user: User) -> str:
    # Imported here rather than at module scope, as in
    # ``tenants.service.create_tenant``: ``backend.auth`` eagerly imports its
    # routes, which import ``auth.service``, which imports this module — so a
    # top-level import of the roles constant closes a cycle.
    from backend.auth.roles import ROLE_OWNER

    return "owner" if user.role == ROLE_OWNER else "member"


def _seats_excluding(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Seats this workspace holds, not counting the one being changed.

    Deliberately not ``count_seats`` minus a guess: excluding the person the
    event is about makes the answer the same whether the caller has already
    staged their grant, their release, or their delete.
    """
    return (
        db.query(User.id)
        .filter(
            User.tenant_id == tenant_id,
            User.seat_granted_at.isnot(None),
            User.id != user_id,
        )
        .count()
    )


def _ever_answered(db: Session, *, user_id: uuid.UUID, since: datetime) -> bool:
    """Did this person write a reply to anybody since they took this seat?

    Over ``messages``, and deliberately not over ``operator_sessions``, which
    would be the smaller read and is where ``operator_session_ended`` gets its
    own ``answered``. A stretch's ``operator_user_id`` is whoever *opened* it —
    stamped once by ``open_operator_session`` and never re-stamped, because two
    colleagues working one thread are one stretch with one clock, and
    ``record_operator_reply`` sets only ``first_reply_at``. So the opener and
    the replier can be two different people, and asking that table who answered
    would credit the reply to the colleague who took the chat and never wrote a
    word, while reporting the one who actually answered as a seat that produced
    nothing. Both errors at once, silently, in exactly the multi-operator
    workspace this property exists to measure.

    ``messages.operator_user_id`` is per-reply attribution, written on every
    operator ingest, so it answers about the person rather than about the
    stretch. NULL there means an unattributed reply — phase 1 accepts an
    inbound e-mail whose From address matches no user — and an answer nobody
    can be credited for is not evidence about anybody's seat.

    Bounded below by ``since``, the moment this seat was granted: a seat given
    back and taken again is a new seat, and an answer from the previous holding
    says nothing about this one.

    That column carries no index, so this is a scan. It runs when a seat is
    released — an administrative action nobody performs in a loop — and never
    on the reply path.
    """
    return (
        db.query(Message.id)
        .filter(
            Message.role == MessageRole.operator,
            Message.operator_user_id == user_id,
            Message.created_at >= since,
        )
        .first()
        is not None
    )


def capture_seat_granted(db: Session, *, user: User) -> SeatChange | None:
    """Describe the grant about to happen. **Call before ``grant_seat``.**

    ``None`` when there is nothing to report: the person already holds a seat
    (an idempotent re-grant is the same seat, not a new one), or they belong
    to no workspace.
    """
    if user.seat_granted_at is not None:
        return None
    tenant_public_id = _tenant_public_id(user)
    if tenant_public_id is None or user.tenant_id is None:
        return None
    return SeatChange(
        event=SEAT_GRANTED,
        tenant_public_id=tenant_public_id,
        user_id=str(user.id),
        holder=_holder(user),
        seats=_seats_excluding(db, tenant_id=user.tenant_id, user_id=user.id) + 1,
    )


def capture_seat_released(
    db: Session, *, user: User, reason: str
) -> SeatChange | None:
    """Describe the release about to happen. **Call before the write.**

    Before ``release_seat``, and before the ``db.delete`` that removes a
    member or a whole workspace: both of the facts this reads are destroyed by
    those writes. See the module docstring.

    ``None`` when the person holds no seat — an idempotent release, or a
    member removed before they ever accepted their invitation — and when they
    belong to no workspace.

    One release deliberately has no call to this at all:
    ``tenants.service.create_tenant`` scrubs a stale ``seat_granted_at`` off a
    founder whose row outlived an earlier workspace. That seat was taken
    somewhere else, in a workspace that no longer exists; reporting it as
    released from the workspace being created now would invent a seat that
    workspace never sold.
    """
    held_since = user.seat_granted_at
    if held_since is None:
        return None
    tenant_public_id = _tenant_public_id(user)
    if tenant_public_id is None or user.tenant_id is None:
        return None
    held_ms = max(0, int((_utcnow() - held_since).total_seconds() * 1000))
    return SeatChange(
        event=SEAT_RELEASED,
        tenant_public_id=tenant_public_id,
        user_id=str(user.id),
        holder=_holder(user),
        seats=_seats_excluding(db, tenant_id=user.tenant_id, user_id=user.id),
        held_ms=held_ms,
        held_days=held_ms // _MS_PER_DAY,
        answered=_ever_answered(db, user_id=user.id, since=held_since),
        reason=reason,
    )


def emit_seat_change(change: SeatChange | None) -> None:
    """Report one seat event. Call only after the commit that made it true.

    Accepts ``None`` so callers can hand on whatever the builders gave them.
    Swallows and logs, like every other emitter here: the seat has already
    changed hands, and telemetry must not be able to fail the action it
    describes.
    """
    if change is None:
        return
    properties: dict[str, object] = {
        "holder": change.holder,
        "user_id": change.user_id,
        "seats": change.seats,
    }
    if change.event == SEAT_RELEASED:
        properties.update(
            {
                "held_ms": change.held_ms,
                "held_days": change.held_days,
                "answered": change.answered,
                "reason": change.reason,
            }
        )
    try:
        capture_event(
            change.event,
            distinct_id=change.tenant_public_id,
            tenant_id=change.tenant_public_id,
            properties=properties,
            groups={"tenant": change.tenant_public_id},
        )
    except Exception:
        logger.warning("Failed to emit %s event", change.event, exc_info=True)
