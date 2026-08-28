"""Hourly background job: delete invitations nobody accepted.

An invite creates a real ``users`` row — unverified, with an unusable password
and a seven-day token — so an unaccepted invitation does not simply fade when
its token dies. The row survives and keeps the address occupied: the invited
person cannot register on their own (``/auth/register`` answers 409), and the
only party who can free the address is the owner who typed it, who has no
reason to think about it again. A typo'd address is then burned indefinitely.

Expiring the token is not enough; the row has to go with it. That also matches
what removing a member now does — no half-joined accounts lying around.

Why not a pass on ``chat_session_sweeper``: those five passes are one ordered
chain about one subject (chats, tickets, operator stretches), and their
docstring documents the interdependencies that fix their order. An invitation
shares no subject and no ordering with any of them, so a sixth pass would mean
a reader of that chain has to discover that one of its steps is about
something else. ``guard_events_purge`` is the existing precedent for the shape
this actually is — delete rows past a retention window on a periodic tick —
and this follows it.

**Interaction with the last-owner guard.** ``count_owners`` counts verified
owners only, so an unaccepted invitation is never what holds a workspace's
owner count above zero, and deleting one can never strand a workspace without
an administrator. The ordering holds in the other direction too: were the
count ever to include pending rows again, this job would become able to
remove a workspace's only "owner" — which is precisely why that filter and
this sweep belong to the same change.

Runs as a :class:`~backend.jobs._periodic.PeriodicJob` daemon thread. Across
workers a Redis distributed lock gates each tick. Without Redis (local dev) it
runs unguarded — single-process safe, and the DELETE is idempotent anyway (a
row deleted once cannot be re-selected).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.jobs._periodic import LockSpec, PeriodicJob
from backend.models import User
from backend.models.base import _utcnow

logger = logging.getLogger(__name__)

_STARTUP_DELAY_SECONDS = 180
_CHECK_INTERVAL_SECONDS = 3600
# Bounds crash recovery only. A purge of this size is far quicker than this;
# a killed holder self-heals well before the next hourly tick.
_LOCK_TTL_SECONDS = 300
# Deleted per committed batch. The population is tiny in practice — a handful
# of typos per workspace — so this is about bounding the pathological case,
# not the normal one.
_BATCH_SIZE = 500


def purge_expired_invitations(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = _BATCH_SIZE,
) -> int:
    """Delete member rows whose invitation expired unaccepted; return the count.

    The predicate is the definition of "invited, never joined":

    * ``tenant_id IS NOT NULL`` — a member row, not someone mid-signup who
      registered for themselves and has no workspace yet. Deleting one of
      those would destroy an account its owner is in the middle of creating.
    * ``is_verified IS FALSE`` — accepting an invite verifies, so this is
      exactly the set that never accepted. It also guarantees the row has no
      history to preserve: every operator surface is behind
      ``require_verified_user``, so an unverified account cannot have written
      a message, held a chat or dismissed a gap. Nothing to stamp.
    * ``reset_password_expires_at < now`` — the invite's own clock, already on
      the row. A NULL expiry never matches, so a member row in an unexpected
      shape is left alone rather than deleted on a guess.

    The cutoff is fixed at call time, so an invitation sent during a run is
    never eligible and the loop always terminates.
    """
    cutoff = now or _utcnow()
    condition = (
        User.tenant_id.isnot(None)
        & User.is_verified.is_(False)
        & User.reset_password_expires_at.isnot(None)
        & (User.reset_password_expires_at < cutoff)
    )

    total = 0
    while True:
        ids = (
            db.execute(select(User.id).where(condition).limit(batch_size))
            .scalars()
            .all()
        )
        if not ids:
            break
        db.execute(sa_delete(User).where(User.id.in_(ids)))
        db.commit()
        total += len(ids)
        if len(ids) < batch_size:
            break
    if total:
        logger.info(
            "expired_invitations_purge: deleted %d unaccepted invitations", total
        )
    return total


def _purge_once() -> None:
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        purge_expired_invitations(db)
    except Exception:
        # Roll back the failed batch and let the error propagate so the loop
        # wrapper logs it and the next tick retries. Batches committed before
        # the failure persist.
        db.rollback()
        raise
    finally:
        db.close()


_job = PeriodicJob(
    name="expired-invitations-purge",
    work=_purge_once,
    interval_seconds=_CHECK_INTERVAL_SECONDS,
    startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    lock=LockSpec(
        job_kind="expired_invitations_purge",
        key_factory=lambda: "lock:expired_invitations_purge",
        ttl_seconds=_LOCK_TTL_SECONDS,
    ),
)


def start_expired_invitations_purge_thread() -> None:
    _job.start()


def shutdown_expired_invitations_purge_thread() -> None:
    _job.shutdown()
