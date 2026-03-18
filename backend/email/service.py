"""Simple SMTP email sender for verification emails."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from backend.core.config import settings


def send_email(to: str, subject: str, body: str) -> None:
    """
    Simple SMTP email sender.

    - If SMTP_HOST is not configured, log the email to console (for dev).
    """
    if not settings.SMTP_HOST or not settings.EMAIL_FROM:
        # Dev fallback: just log the message
        print("=== EMAIL (DEV MODE) ===")
        print("To:", to)
        print("Subject:", subject)
        print("Body:\n", body)
        print("========================")
        return

    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT or 587) as server:
        server.starttls()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
