"""Tests for PII redaction at the model egress boundary."""

from __future__ import annotations

import pytest

from backend.chat.pii import (
    DetectedEntitySummary,
    redact,
    redact_text,
)


def test_redact_email():
    assert (
        redact_text("my email is user@example.com please help")
        == "my email is [EMAIL] please help"
    )
    assert redact_text("contact support@company.co.uk") == "contact [EMAIL]"


@pytest.mark.parametrize(
    "text,marker",
    [
        ("звони на +7 (999) 123-45-67", "[PHONE]"),
        ("мой номер 8-999-123-45-67", "[PHONE]"),
        ("+79991234567", "[PHONE]"),
        ("call me at +1-800-555-0100", "[PHONE]"),
        ("my key is sk-abc123XYZ789verylongkeyhere1234", "[API_KEY]"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123", "[API_KEY]"),
        ("https://example.com/reset?token=abc123456", "[URL_TOKEN]"),
        ("connecting from 192.168.1.10", "[IP]"),
    ],
    ids=[
        "phone-ru-spaced",
        "phone-ru-dashed",
        "phone-ru-e164",
        "phone-international",
        "api-key-sk-prefix",
        "api-key-bearer-jwt",
        "url-token",
        "ip",
    ],
)
def test_redact_text_detects_entity(text, marker):
    assert marker in redact_text(text)


def test_redact_credit_cards_with_luhn():
    assert "[CARD]" in redact_text("card: 4111 1111 1111 1111")
    assert "[CARD]" in redact_text("4111111111111111")
    assert "[CARD]" not in redact_text("4111111111111112")
    assert redact_text("number 1234 5678 9012 3456") == "number 1234 5678 9012 3456"


@pytest.mark.parametrize(
    "text",
    [
        "8(495)123-45-67 4111 1111 1111 1111",
        "4111111111111111 8(495)123-45-67",
        "192.168.1.100 4111111111111111",
        "4111111111111111 192.168.1.100",
        "10.0.0.254 4111 1111 1111 1111",
        "+7 916 123-45-67 4111 1111 1111 1111",
        "4111 1111 1111 1111 99",
    ],
)
def test_card_next_to_another_number_is_still_masked(text):
    """A neighbouring number must not swallow the card into a failing checksum.

    A phone number or an IP written beside a card forms one digit run with it.
    Testing that run as a whole fails Luhn, and dropping it left the full card
    number in the text sent to the model.
    """
    result = redact_text(text)
    assert "[CARD]" in result
    assert "4111" not in result


def test_redaction_result_contains_entity_counts():
    result = redact("mail me at test@email.com and backup@email.com")
    assert result.was_redacted is True
    assert result.entities_found == [DetectedEntitySummary(type="EMAIL", count=2)]


def test_was_redacted_flag():
    assert redact("send to test@email.com").was_redacted is True
    assert redact("how do I reset my password?").was_redacted is False


def test_multiple_entities():
    result = redact_text("I'm John, email test@test.com, phone +79991234567")
    assert "[EMAIL]" in result
    assert "[PHONE]" in result
    assert "test@test.com" not in result
    assert "+79991234567" not in result


def test_an_entity_inside_another_match_does_not_split_the_mask():
    """A narrower match must not strand the rest of a value outside the mask."""
    result = redact_text("reach me at 2024@home.net or +79161234567")
    assert "2024" not in result
    assert "79161234567" not in result


@pytest.mark.parametrize(
    "text",
    [
        "see https://example.com/docs for details",
        "release 1.2.3.4 and invalid ip 999.999.999.999",
        "My order number is 100234567, when will it ship?",
        "I get error code ERR5012 when saving",
        "Ticket ESC-2024-0198 was closed",
        "Rate limit: 100000 requests per day",
        "Build 20240115 crashed on startup",
        "SKU 987654321 out of stock",
        "how do I reset my password?",
        "my password is Hunter22",
        "mein Passwort ist Hunter22",
        "паспорт 4510 123456",
        "инн 7707083893",
    ],
    ids=[
        "plain-url",
        "invalid-ip-octets",
        "order-number",
        "error-code",
        "ticket-id",
        "rate-limit-number",
        "build-number",
        "sku",
        "benign-question",
        "labelled-secret-en",
        "labelled-secret-de",
        "labelled-secret-ru-passport",
        "labelled-secret-ru-inn",
    ],
)
def test_redact_text_is_a_noop_for_non_pii(text):
    """Out-of-scope inputs (plain URLs, business identifiers, labelled secrets
    that would need a language-specific label to catch) pass through unchanged."""
    assert redact_text(text) == text
