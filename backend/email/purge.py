"""Delete a workspace's addresses from Brevo.

Brevo is where the e-mail side of a workspace accumulates: sending to an
address through the transactional API leaves a contact behind for it. For a
departing workspace that set is its members' addresses, the support inbox its
escalations were routed to, and the visitors' addresses those escalations
carried as ``Reply-To``. All of it is personal data we hold only because that
workspace existed, so it goes when the workspace does.

Contacts are account-wide in Brevo, not per workspace, so an address can be one
we still hold for somebody else — the same person owning a second workspace, or
a visitor who also wrote to a workspace that is staying. The caller passes the
addresses; :func:`delete_contacts` deletes exactly those, and the *decision* of
which addresses are still ours to keep is made against our own database in
``backend/jobs/workspace_purge.py`` before the call.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
_TIMEOUT_SECONDS = 15.0


def brevo_purge_configured() -> bool:
    """Whether we have a Brevo account to delete from at all."""
    return bool(settings.BREVO_API_KEY)


async def delete_contacts(emails: list[str]) -> int:
    """Delete each address's Brevo contact; return how many were removed.

    A 404 counts as success — the address is not in Brevo, which is the state
    we are asking for. Anything else raises, so the caller's retry policy can
    take over; the call is idempotent, so a retry that re-deletes an address
    already gone is harmless.
    """
    if not brevo_purge_configured():
        logger.info("brevo_purge_skipped reason=not_configured addresses=%d", len(emails))
        return 0
    if not emails:
        return 0

    deleted = 0
    headers = {"api-key": settings.BREVO_API_KEY, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        for email in emails:
            url = f"{_CONTACTS_URL}/{quote(email, safe='')}"
            response = await client.delete(url, headers=headers)
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                # No address in the message: it would put the very PII this
                # job exists to remove into ``background_jobs.last_error``.
                raise RuntimeError(
                    f"Brevo contact delete failed with status {response.status_code}"
                )
            deleted += 1

    logger.info("brevo_purge_done requested=%d deleted=%d", len(emails), deleted)
    return deleted
