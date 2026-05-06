"""Unit tests for sanitize_user_hints (untrusted browser-supplied fields)."""

from __future__ import annotations

import pytest

from backend.widget.service import sanitize_user_hints


def test_drops_unknown_keys() -> None:
    result = sanitize_user_hints(
        {"name": "Anna", "password": "secret", "ssn": "123-45-6789"}
    )
    assert result == {"name": "Anna"}


def test_caps_oversized_values() -> None:
    long_name = "x" * 500
    result = sanitize_user_hints({"name": long_name})
    assert result["name"] == "x" * 200  # _IDENTITY_FIELD_CAPS["name"] = 200


def test_drops_empty_and_whitespace() -> None:
    result = sanitize_user_hints({"name": "   ", "email": "", "plan_tier": "growth"})
    assert result == {"plan_tier": "growth"}


def test_rejects_malformed_email() -> None:
    result = sanitize_user_hints({"email": "not-an-email", "name": "Anna"})
    assert "email" not in result
    assert result == {"name": "Anna"}


def test_accepts_plausible_email() -> None:
    result = sanitize_user_hints({"email": "anna@example.com"})
    assert result == {"email": "anna@example.com"}


def test_rejects_invalid_locale() -> None:
    result = sanitize_user_hints({"locale": "not a locale"})
    assert "locale" not in result


def test_accepts_bcp47_locale() -> None:
    result = sanitize_user_hints({"locale": "ru-RU"})
    assert result == {"locale": "ru-RU"}


def test_returns_empty_for_none() -> None:
    assert sanitize_user_hints(None) == {}


def test_returns_empty_for_non_dict() -> None:
    assert sanitize_user_hints("not a dict") == {}
    assert sanitize_user_hints([]) == {}


@pytest.mark.parametrize(
    "key", ["user_id", "email", "name", "plan_tier", "audience_tag", "locale"]
)
def test_all_allowed_keys_pass_through(key: str) -> None:
    payload = {
        "user_id": "u-123",
        "email": "x@y.com",
        "name": "X",
        "plan_tier": "free",
        "audience_tag": "b2b",
        "locale": "en-US",
    }
    result = sanitize_user_hints({key: payload[key]})
    assert key in result


def test_full_valid_payload() -> None:
    result = sanitize_user_hints(
        {
            "user_id": "ext-42",
            "email": "anna@example.com",
            "name": "Anna",
            "plan_tier": "growth",
            "audience_tag": "b2b",
            "locale": "ru-RU",
        }
    )
    assert result == {
        "user_id": "ext-42",
        "email": "anna@example.com",
        "name": "Anna",
        "plan_tier": "growth",
        "audience_tag": "b2b",
        "locale": "ru-RU",
    }
