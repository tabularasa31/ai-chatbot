"""Tests for PII regex redaction (FI-043)."""

from backend.chat.pii import redact, redact_text


def test_redact_email():
    assert (
        redact_text("my email is user@example.com please help")
        == "my email is [EMAIL] please help"
    )
    assert redact_text("contact support@company.co.uk") == "contact [EMAIL]"


def test_redact_phone_ru():
    assert "[PHONE]" in redact_text("звони на +7 (999) 123-45-67")
    assert "[PHONE]" in redact_text("мой номер 8-999-123-45-67")
    assert "[PHONE]" in redact_text("+79991234567")


def test_redact_phone_international():
    assert "[PHONE]" in redact_text("call me at +1-800-555-0100")


def test_redact_api_keys():
    assert "[API_KEY]" in redact_text(
        "my key is sk-abc123XYZ789verylongkeyhere1234"
    )
    assert "[API_KEY]" in redact_text(
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123"
    )


def test_redact_credit_cards():
    assert "[CREDIT_CARD]" in redact_text("card: 4111 1111 1111 1111")
    assert "[CREDIT_CARD]" in redact_text("4111111111111111")


def test_no_false_positives_on_normal_text():
    text = "how do I reset my password?"
    assert redact_text(text) == text


def test_was_redacted_flag():
    _, was_redacted = redact("send to test@email.com")
    assert was_redacted is True

    _, was_redacted = redact("how do I reset my password?")
    assert was_redacted is False


def test_multiple_entities():
    result = redact_text(
        "I'm John, email test@test.com, phone +79991234567"
    )
    assert "[EMAIL]" in result
    assert "[PHONE]" in result
    assert "test@test.com" not in result
    assert "+79991234567" not in result
