"""Durable ARQ job: erase a deleted workspace from the systems outside our DB.

Deleting a workspace destroys its rows in one transaction, but two third-party
systems hold data of their own and neither is reachable transactionally:

* **Langfuse** — the conversations themselves (question previews, answers).
* **Brevo** — the addresses a workspace's existence puts into our mail
  account: its members', and the support inbox its escalations are routed to.
  Visitors' addresses are included too, with a caveat worth knowing: today
  they only ever appear as ``replyTo`` on an escalation notification, and
  Brevo creates transactional contacts from the recipient rather than from
  ``replyTo`` — so most of them are not contacts yet and their deletes come
  back 404. They are in scope anyway, because the owner's decision covers
  them and because the inbound e-mail lane being built alongside this makes
  visitors real recipients. The guard below is what keeps that breadth safe.

PostHog is deliberately not on that list. It holds behavioural metadata only —
identifiers, durations, outcomes — with no conversation text and no personal
data, and product metrics stay comparable across time rather than being
rewritten every time a workspace leaves.

Ordering
--------
The hazard is that an external call fails *after* the local delete has
committed: the workspace is gone from our database, the data survives
elsewhere, and no row is left to retry from. So the job is enqueued **before**
the local delete, and everything it needs outlives that delete in
``background_jobs`` — a table whose rows the delete cannot reach.

Two consequences of that ordering, both handled here:

* The job must not act while the workspace still exists — the local delete
  might not have committed yet, or might have failed. It checks, and retries
  rather than proceeding. Erring that way leaves external data behind for a
  workspace that is still live and still deletable, rather than destroying the
  traces of a workspace its owner still has.
* It must not silently succeed in that case either. A no-op that reports
  ``completed`` would be a cleanup that never happened, recorded as one that
  did. Exhausting the retries lands the row on ``dead_letter`` with a message
  saying the workspace was still there.

Retries: arq does not do what its name suggests
-----------------------------------------------
arq re-queues a job **only** for ``Retry``, ``RetryJob`` and
``CancelledError`` (``arq/worker.py`` — every other exception takes the
``else:`` branch, which sets ``finish = True``). A plain ``raise`` is a
*permanent* failure, and ``max_tries`` is never consulted because the job is
never re-queued. So every failure path here raises :class:`arq.Retry`
explicitly; that is what makes ``max_attempts`` on the decorator mean anything,
and what lets the final attempt reach ``dead_letter`` rather than ``failed``.

This is the first job in the repo to depend on retries — ``crawl_url_source``
is re-driven by its own cron and ``knowledge_extraction`` never raises — so
there was no working precedent to copy.

Why the payload rather than the arguments
-----------------------------------------
The addresses are read from ``background_jobs.payload``, not passed as job
arguments, for two independent reasons:

* **Arguments are logged.** ``Worker._run_job`` logs every job's arguments at
  INFO on each start (``args_to_string``, truncated to 80 characters). Passing
  the list would print visitors' addresses into the worker's log stream — and,
  via Sentry's INFO breadcrumbs, attach them to any later error event. On the
  one code path whose entire purpose is removing those addresses.
* **Arguments do not survive.** arq keeps the serialized job for
  ``keep_result_seconds`` (1 h). An hour after a failure the addresses would be
  gone from Redis and the rows they came from deleted, leaving nothing to retry
  by hand. ``background_jobs`` is Postgres and keeps them.

The status row is written with ``tenant_id=None`` on purpose.
``background_jobs.tenant_id`` is ``ON DELETE CASCADE`` to ``tenants``, so
stamping it would have the local delete cascade away the very row that carries
the addresses and records the outstanding cleanup.

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

from arq import Retry
from sqlalchemy import func, or_, select

from backend.core import db as core_db
from backend.core.queue import enqueue, get_main_loop, register_job
from backend.email.purge import brevo_purge_configured, delete_contacts
from backend.models import BackgroundJob, EscalationTicket, Tenant, User
from backend.observability.langfuse_purge import (
    delete_traces_for_tenant,
    langfuse_purge_configured,
)
from backend.support_config import public_support_config_dict

logger = logging.getLogger(__name__)

_JOB_NAME = "purge_workspace_external_data"
_MAX_ATTEMPTS = 5

# See "Ordering" above: long enough that the local delete has normally
# committed, short enough that a departing owner's data is not sitting in
# Langfuse for any length of time worth naming.
_ENQUEUE_DELAY_SECONDS = 30

# Backoff when the workspace is still present. The delete is not instant — its
# cascade unwinds chats, messages, embeddings and documents, and can queue
# behind a lock held by an in-flight chat turn — so the first look is not
# proof that it failed. Five of these covers well over an hour.
_TENANT_PRESENT_RETRY_SECONDS = 120

# Backoff when a vendor call failed. Long enough to ride out a redeploy.
_VENDOR_RETRY_SECONDS = 300

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
async def purge_workspace_external_data(ctx: dict[str, Any], tenant_id: str) -> None:
    """Erase the workspace from Langfuse and Brevo.

    Raises :class:`arq.Retry` on any failure — see the module docstring for why
    a plain exception would not be retried. Both purges are idempotent, so a
    retry re-runs the one that succeeded without harm; both are attempted on
    every pass rather than stopping at the first error, so one broken vendor
    does not hold the other's data hostage for the length of the backoff.
    """
    job_id = str(ctx.get("job_id", ""))
    emails = await _load_addresses(job_id)

    if await _tenant_still_exists(tenant_id):
        # The delete has not landed. Retry rather than proceed, and rather than
        # return — a silent no-op here is the cleanup never happening, recorded
        # as though it had.
        logger.warning(
            "workspace_purge_deferred reason=tenant_still_present tenant_id=%s",
            tenant_id,
        )
        raise Retry(defer=_TENANT_PRESENT_RETRY_SECONDS)

    keep = await _addresses_still_referenced(emails, purged_tenant_id=tenant_id)
    to_delete = [email for email in emails if email.lower() not in keep]

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
        # Type names only, never the exception's own message: a vendor client's
        # message can quote the request, and the request is an address.
        logger.warning(
            "workspace_purge_incomplete tenant_id=%s failures=%s",
            tenant_id,
            "; ".join(failures),
        )
        raise Retry(defer=_VENDOR_RETRY_SECONDS)

    logger.info(
        "workspace_purge_done tenant_id=%s addresses=%d retained=%d",
        tenant_id,
        len(to_delete),
        len(keep),
    )


async def _load_addresses(job_id: str) -> list[str]:
    """Read this job's addresses back out of its own ``background_jobs`` row."""
    if not job_id:
        raise Retry(defer=_VENDOR_RETRY_SECONDS)
    async with core_db.AsyncSessionLocal() as db:
        found = await db.execute(
            select(BackgroundJob.payload).where(BackgroundJob.arq_job_id == job_id)
        )
        payload = found.scalars().first()
    if not isinstance(payload, dict):
        # Nothing to work from, and nothing a retry can conjure up.
        raise RuntimeError(f"workspace purge job {job_id} has no payload row")
    addresses = payload.get("addresses") or []
    return [a for a in addresses if isinstance(a, str) and a]


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
    """Of ``emails``, the lowercased ones we still hold for somebody staying.

    A Brevo contact is account-wide, not per workspace, so deleting one on
    behalf of a departing workspace takes it from every workspace that shares
    the address — and deleting a contact also discards its unsubscribe state,
    so a previously opted-out address becomes sendable again. This is the guard
    against that, and it has to cover every way an address can still be ours:

    * a live account, including one with no workspace yet (somebody mid-signup);
    * an escalation ticket belonging to a workspace that is staying;
    * **another workspace's configured support inbox** — an address that
      appears in no ``users`` row and no ticket at all, which is exactly the
      shape an agency's shared inbox has across the workspaces it runs.

    Matching is case-insensitive on both sides. Nothing normalises an address
    on the way in — ``register_user`` stores what was typed, and a widget's
    ``user_context`` supplies ticket addresses verbatim — so ``Foo@x.com`` and
    ``foo@x.com`` are the same mailbox to Brevo but different strings to SQL.

    Rows of ``purged_tenant_id`` are excluded rather than assumed absent, so
    the check reads the same whether or not the delete has landed yet.

    Wrapped against database errors, which would otherwise carry the bound
    parameters — the addresses themselves — into ``background_jobs.last_error``
    and Sentry. Same reasoning as ``escalation.service`` keeps for its own
    failure reports: the type name is enough to triage, the message is not
    ours to store.
    """
    if not emails:
        return set()

    lowered = sorted({e.lower() for e in emails})
    purged = uuid.UUID(purged_tenant_id)

    try:
        async with core_db.AsyncSessionLocal() as db:
            users = await db.execute(
                select(func.lower(User.email)).where(
                    func.lower(User.email).in_(lowered),
                    or_(User.tenant_id.is_(None), User.tenant_id != purged),
                )
            )
            tickets = await db.execute(
                select(func.lower(EscalationTicket.user_email)).where(
                    func.lower(EscalationTicket.user_email).in_(lowered),
                    EscalationTicket.tenant_id != purged,
                )
            )
            # Support inboxes live inside a JSON blob, where a portable
            # predicate is more trouble than it is worth. The surviving tenant
            # count is small — one workspace per owner — so this reads them and
            # filters in Python rather than reaching for dialect-specific JSON
            # operators that behave differently on SQLite and Postgres.
            settings_rows = await db.execute(
                select(Tenant.settings).where(Tenant.id != purged)
            )
            inboxes = {
                inbox.lower()
                for row in settings_rows.scalars()
                if (
                    inbox := public_support_config_dict(
                        row if isinstance(row, dict) else None
                    ).get("l2_email")
                )
            }
    except Exception as exc:
        raise RuntimeError(
            f"workspace purge reference check failed: {type(exc).__name__}"
        ) from None

    referenced = {row for row in users.scalars() if row}
    referenced |= {row for row in tickets.scalars() if row}
    referenced |= {inbox for inbox in inboxes if inbox in set(lowered)}
    return referenced


async def enqueue_workspace_purge(
    *,
    tenant_id: uuid.UUID,
    emails: list[str],
) -> str | None:
    """Schedule the cleanup. Returns the ARQ job id, or None if Redis is down.

    The addresses go in ``payload`` and the tenant id is echoed there too —
    neither becomes a job argument, and neither becomes the status row's
    ``tenant_id`` column. See the module docstring for both reasons.
    """
    return await enqueue(
        _JOB_NAME,
        str(tenant_id),
        kind=_JOB_NAME,
        payload={"tenant_id": str(tenant_id), "addresses": emails},
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
        # No address in the log line: tenant id only.
        logger.warning(
            "workspace_purge_enqueue_failed tenant_id=%s", tenant_id, exc_info=False
        )
        return None
