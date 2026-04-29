"""Email sender via Brevo HTTP API."""

from __future__ import annotations

import logging

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    reply_to: str | None = None,
) -> None:
    """Send email via Brevo HTTP API.

    - If BREVO_API_KEY or EMAIL_FROM is not configured, log email in dev mode.
    - Network/API errors are logged but do NOT raise (to avoid breaking signup).
    - ``reply_to`` lets the recipient reply directly to a third party (e.g. the
      end-user behind an escalation ticket) by pressing "Reply" in their client.
    """
    if not settings.BREVO_API_KEY or not settings.EMAIL_FROM:
        # Dev fallback: log email instead of sending
        logger.info(
            "EMAIL DEV MODE: to=%s subject=%s reply_to=%s body=%s",
            to,
            subject,
            reply_to,
            body,
        )
        return

    payload: dict = {
        "sender": {"email": settings.EMAIL_FROM},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}

    try:
        resp = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": settings.BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10.0,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Brevo email send failed: status=%s body=%s",
                resp.status_code,
                resp.text,
            )
    except Exception as e:
        logger.warning("Failed to send email via Brevo: %s", e)
