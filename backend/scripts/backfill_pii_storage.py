"""Backfill encrypted original and redacted text fields for existing records."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.chat.pii import redact
from backend.core.crypto import encrypt_value
from backend.core.db import SessionLocal
from backend.models import EscalationTicket, Message
from backend.privacy_config import public_redaction_config_dict


def _message_optional_entity_types(message: Message) -> set[str] | None:
    if not message.chat or not message.chat.client or not isinstance(message.chat.client.settings, dict):
        return None
    cfg = public_redaction_config_dict(message.chat.client.settings)
    return set(cfg["optional_entity_types"])


def _ticket_optional_entity_types(ticket: EscalationTicket) -> set[str] | None:
    if not ticket.client or not isinstance(ticket.client.settings, dict):
        return None
    cfg = public_redaction_config_dict(ticket.client.settings)
    return set(cfg["optional_entity_types"])


def run(db: Session) -> None:
    messages = db.query(Message).all()
    for message in messages:
        if not message.content_original_encrypted:
            message.content_original_encrypted = encrypt_value(message.content)
        if not message.content_redacted:
            message.content_redacted = redact(
                message.content,
                optional_entity_types=_message_optional_entity_types(message),
            ).redacted_text
        if message.content_redacted:
            message.content = message.content_redacted

    tickets = db.query(EscalationTicket).all()
    for ticket in tickets:
        if not ticket.primary_question_original_encrypted:
            ticket.primary_question_original_encrypted = encrypt_value(ticket.primary_question)
        if not ticket.primary_question_redacted:
            ticket.primary_question_redacted = redact(
                ticket.primary_question,
                optional_entity_types=_ticket_optional_entity_types(ticket),
            ).redacted_text
        if ticket.primary_question_redacted:
            ticket.primary_question = ticket.primary_question_redacted
    db.commit()


if __name__ == "__main__":
    db = SessionLocal()
    try:
        run(db)
    finally:
        db.close()
