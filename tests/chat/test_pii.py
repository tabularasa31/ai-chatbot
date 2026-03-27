"""Tests for PII regex redaction."""

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


def test_redact_password_id_doc_ip_and_url_token():
    result = redact_text(
        "password is Hunter22 passport 4510 123456 ip 192.168.1.10 "
        "https://example.com/reset?token=abc123456"
    )
    assert "[PASSWORD]" in result
    assert "[ID_DOC]" in result
    assert "[IP]" in result
    assert "[URL_TOKEN]" in result


def test_redact_credit_cards_with_luhn():
    assert "[CARD]" in redact_text("card: 4111 1111 1111 1111")
    assert "[CARD]" in redact_text("4111111111111111")
    assert redact_text("number 1234 5678 9012 3456") == "number 1234 5678 9012 3456"


def test_optional_entity_types_can_be_disabled():
    result = redact_text(
        "паспорт 4510 123456 from 192.168.1.10",
        optional_entity_types={"IP"},
    )
    assert "[IP]" in result
    assert "[ID_DOC]" not in result


def test_no_false_positives_on_normal_text():
    text = "how do I reset my password?"
    assert redact_text(text) == text


def test_redaction_result_contains_entity_counts():
    result = redact("mail me at test@email.com and backup@email.com")
    assert result.was_redacted is True
    assert result.entities_found == [DetectedEntitySummary(type="EMAIL", count=2)]


def test_was_redacted_flag():
    result = redact("send to test@email.com")
    assert result.was_redacted is True

    result = redact("how do I reset my password?")
    assert result.was_redacted is False


def test_multiple_entities():
    result = redact_text(
        "I'm John, email test@test.com, phone +79991234567"
    )
    assert "[EMAIL]" in result
    assert "[PHONE]" in result
    assert "test@test.com" not in result
    assert "+79991234567" not in result
