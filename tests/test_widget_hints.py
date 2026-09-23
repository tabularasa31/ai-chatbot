"""Unit tests for sanitize_user_hints (untrusted browser-supplied fields)."""

from __future__ import annotations

import pytest

from backend.widget.service import sanitize_user_hints

_FULL_VALID_PAYLOAD = {
    "user_id": "ext-42",
    "email": "anna@example.com",
    "name": "Anna",
    "plan_tier": "growth",
    "audience_tag": "b2b",
    "locale": "ru-RU",
}


@pytest.mark.parametrize(
    "hints,expected",
    [
        pytest.param(
            {"name": "Anna", "password": "secret", "ssn": "123-45-6789"},
            {"name": "Anna"},
            id="drops_unknown_keys",
        ),
        pytest.param({"name": "x" * 500}, {"name": "x" * 200}, id="caps_oversized_values"),
        pytest.param(
            {"name": "   ", "email": "", "plan_tier": "growth"},
            {"plan_tier": "growth"},
            id="drops_empty_and_whitespace",
        ),
        pytest.param(
            {"email": "not-an-email", "name": "Anna"}, {"name": "Anna"}, id="rejects_malformed_email"
        ),
        pytest.param(
            {"email": "anna@example.com"}, {"email": "anna@example.com"}, id="accepts_plausible_email"
        ),
        pytest.param({"locale": "not a locale"}, {}, id="rejects_invalid_locale"),
        pytest.param({"locale": "ru-RU"}, {"locale": "ru-RU"}, id="accepts_bcp47_locale"),
        pytest.param(None, {}, id="returns_empty_for_none"),
        pytest.param("not a dict", {}, id="returns_empty_for_non_dict_string"),
        pytest.param([], {}, id="returns_empty_for_non_dict_list"),
        pytest.param(_FULL_VALID_PAYLOAD, _FULL_VALID_PAYLOAD, id="full_valid_payload_passes_through"),
    ],
)
def test_sanitize_user_hints_rules(hints, expected) -> None:
    assert sanitize_user_hints(hints) == expected


@pytest.mark.parametrize(
    "key", ["user_id", "email", "name", "plan_tier", "audience_tag", "locale"]
)
def test_all_allowed_keys_pass_through(key: str) -> None:
    result = sanitize_user_hints({key: _FULL_VALID_PAYLOAD[key]})
    assert key in result
