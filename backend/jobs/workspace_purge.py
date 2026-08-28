"""Durable ARQ job: erase a deleted workspace from the systems outside our DB.

Deleting a workspace destroys its rows in one transaction, but two third-party
systems hold data of their own and neither is reachable transactionally:

* **Langfuse** — the conversations themselves (question previews, answers).
* **Brevo** — addresses: members, the support inbox, and the visitors whose
  e-mails rode along on escalation notifications as ``Reply-To``.

PostHog is deliberately not on that list. It holds behavioural metadata only —
identifiers, durations, outcomes — with no conversation text and no personal
data, and product metrics stay comparable across time rather than being
rewritten every time a workspace leaves.

Ordering
--------
The hazard is that an external call fails *after* the local delete has
committed: the workspace is gone from our database, the data survives
elsewhere, and no row is left to retry from. So the job is enqueued **before**
the local delete, and it carries everything it needs — tenant id and addresses
— as arguments. From then on the retry lives in ``background_jobs``, not in a
row that the delete is about to destroy: ARQ retries with backoff, and a run
that exhausts its attempts lands as ``dead_letter`` with the payload intact and
re-runnable by hand.

Two consequences of that ordering, both handled here:

* The job must not act while the workspace still exists — the local delete
  might have failed or rolled back after we enqueued. It checks, and no-ops if
  the tenant row is still there. Erring that way leaves external data behind
  for a workspace that is still live and still deletable, rather than
  destroying the traces of a workspace its owner still has.
* It must not run *before* the delete commits, or that check would no-op on a
  deletion that then succeeds. Hence ``_ENQUEUE_DELAY_SECONDS``, which is
  enormous next to the milliseconds between the enqueue and the commit.

The status row is written with ``tenant_id=None`` on purpose.
``background_jobs.tenant_id`` is ``ON DELETE CASCADE`` to ``tenants``, so
stamping it would have the local delete cascade away the very row that records
the outstanding cleanup, seconds after it was written. The tenant id lives in
``payload`` instead, where nothing collects it.

Scope
-----
This reaches everything *we* hold for the workspace. It cannot reach copies
already taken out of those systems by someone else — an exported eval dataset,
a downloaded CSV, a screenshot in a ticket. Customer-facing copy says
"everything we hold for this workspace" for that reason; "nowhere does it
remain" would be a promise nobody can keep.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import or_, select

from backend.core import db as core_db
from backend.core.queue import enqueue, get_main_loop, register_job
from backend.email.purge import brevo_purge_configured, delete_contacts
from backend.models import EscalationTicket, Tenant, User
from backend.observability.langfuse_purge import (
    delete_traces_for_tenant,
    langfuse_purge_configured,
)

logger = logging.getLogger(__name__)

_JOB_NAME = "purge_workspace_external_data"
_MAX_ATTEMPTS = 5

# See "Ordering" above: long enough that the local delete has certainly
# committed (or certainly failed), short enough that a departing owner's data
# is not sitting in Langfuse for any length of time worth naming.
_ENQUEUE_DELAY_SECONDS = 30

# How long the sync bridge waits for the enqueue to come back. The caller is an
# HTTP handler that must not proceed to a destructive delete until it knows the
# cleanup is durably scheduled, so this is a wait, not a fire-and-forget.
_ENQUEUE_SYNC_TIMEOUT_SECONDS = 5.0


def external_purge_needed() -> bool:
    """Whether any external system is configured to purge from.

    False in tests and in a local dev environment with no Langfuse or Brevo
    credentials — there is nothing out there, so a workspace deletion needs no
    cleanup job and must not be blocked on being able to schedule one.
    """
    return langfuse_purge_configured() or brevo_purge_configured()


@register_job(name=_JOB_NAME, max_attempts=_MAX_ATTEMPTS)
async def purge_workspace_external_data(
    ctx: dict[str, Any],
    tenant_id: str,
    emails: list[str],
) -> None:
    """Erase the workspace from Langfuse and Brevo.

    Raises on any failure so ARQ retries. Both purges are idempotent, so a
    retry re-runs the one that succeeded without harm; both are attempted on
    every pass rather than stopping at the first error, so one broken vendor
    does not hold the other's data hostage for the length of the backoff.
    """
    if await _tenant_still_exists(tenant_id):
        # The delete that scheduled this did not happen. Leave everything
        # alone — the workspace is live and its owner can delete it again.
        logger.warning(
            "workspace_purge_aborted reason=tenant_still_exists tenant_id=%s",
            tenant_id,
        )
        return

    keep = await _addresses_still_referenced(emails, purged_tenant_id=tenant_id)
    to_delete = [email for email in emails if email not in keep]

    failures: list[str] = []

    try:
        await delete_traces_for_tenant(tenant_id)
    except Exception as exc:
        logger.warning(
            "workspace_purge_langfuse_failed tenant_id=%s error=%s",
            tenant_id,
            type(exc).__name__,
        )
        failures.append(f"langfuse: {type(exc).__name__}")

    try:
        await delete_contacts(to_delete)
    except Exception as exc:
        logger.warning(
            "workspace_purge_brevo_failed tenant_id=%s error=%s",
            tenant_id,
            type(exc).__name__,
        )
        failures.append(f"brevo: {type(exc).__name__}")

    if failures:
        raise RuntimeError(
            f"workspace purge incomplete for {tenant_id} — {'; '.join(failures)}"
        )

    logger.info(
        "workspace_purge_done tenant_id=%s addresses=%d retained=%d",
        tenant_id,
        len(to_delete),
        len(keep),
    )


async def _tenant_still_exists(tenant_id: str) -> bool:
    async with core_db.AsyncSessionLocal() as db:
        found = await db.execute(
            select(Tenant.id).where(Tenant.id == uuid.UUID(tenant_id))
        )
        return found.first() is not None


async def _addresses_still_referenced(
    emails: list[str],
    *,
    purged_tenant_id: str,
) -> set[str]:
    """Of ``emails``, the ones we still hold for somebody who is staying.

    A Brevo contact is account-wide, so deleting one on behalf of a departing
    workspace would take it from every workspace that shares the address. An
    address is kept when it still belongs to a live account (including one with
    no workspace yet — somebody mid-signup) or to an escalation ticket of a
    workspace that is staying.

    Rows of ``purged_tenant_id`` are excluded rather than assumed absent, so
    the check reads the same whether or not the delete has landed yet.
    """
    if not emails:
        return set()

    purged = uuid.UUID(purged_tenant_id)
    async with core_db.AsyncSessionLocal() as db:
        users = await db.execute(
            select(User.email).where(
                User.email.in_(emails),
                or_(User.tenant_id.is_(None), User.tenant_id != purged),
            )
        )
        tickets = await db.execute(
            select(EscalationTicket.user_email).where(
                EscalationTicket.user_email.in_(emails),
                EscalationTicket.tenant_id != purged,
            )
        )
    return {row for row in users.scalars() if row} | {
        row for row in tickets.scalars() if row
    }


async def enqueue_workspace_purge(
    *,
    tenant_id: uuid.UUID,
    emails: list[str],
) -> str | None:
    """Schedule the cleanup. Returns the ARQ job id, or None if Redis is down.

    ``tenant_id`` is passed as an argument and echoed into ``payload``, never
    as the status row's ``tenant_id`` column — see the module docstring.
    """
    return await enqueue(
        _JOB_NAME,
        str(tenant_id),
        emails,
        kind=_JOB_NAME,
        payload={"tenant_id": str(tenant_id), "addresses": len(emails)},
        _defer_by=_ENQUEUE_DELAY_SECONDS,
    )


def enqueue_workspace_purge_sync(
    *,
    tenant_id: uuid.UUID,
    emails: list[str],
) -> str | None:
    """Sync bridge for the ``DELETE /tenants/{tenant_id}`` handler.

    Same shape as ``enqueue_crawl_for_source_sync``: submit the coroutine to
    the main event loop and wait. Returns None when the loop is unavailable or
    the enqueue fails — and unlike a crawl, the caller must treat that as fatal
    rather than shrug: an unscheduled cleanup is data left behind with nothing
    left to find it by.
    """
    loop = get_main_loop()
    if loop is None or not loop.is_running():
        logger.warning(
            "workspace_purge_enqueue_skipped reason=no_loop tenant_id=%s", tenant_id
        )
        return None
    future = asyncio.run_coroutine_threadsafe(
        enqueue_workspace_purge(tenant_id=tenant_id, emails=emails), loop
    )
    try:
        return future.result(timeout=_ENQUEUE_SYNC_TIMEOUT_SECONDS)
    except Exception:
        logger.warning(
            "workspace_purge_enqueue_failed tenant_id=%s", tenant_id, exc_info=True
        )
        return None
