"""Tests for backend.knowledge.entity_extractor.

Covers the public surface of Step 2 of the entity-aware retrieval epic
(ClickUp 86exe5pjx):

- ``extract_entities_from_query`` — hot-path NER with wall-clock timeout
- ``extract_entities_from_passage`` — indexing-time NER, no hot-path timeout

Every error path (timeout, retry-exhausted, JSON garbage, missing key,
permanent OpenAI error) must degrade to ``[]`` — never raise into the
chat pipeline. We assert that explicitly because the hybrid retriever
relies on the empty-list fallback to keep dense + BM25 working when the
entity channel is unavailable.

OpenAI is not exercised; we patch ``get_openai_client`` and
``call_openai_with_retry`` at the module level. This keeps the suite in
the SQLite-only fast lane (no docker required).
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.knowledge import entity_extractor
from backend.knowledge.entity_extractor import (
    extract_entities_from_passage,
    extract_entities_from_query,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _completion(payload: dict | str) -> MagicMock:
    """Build a fake ChatCompletion mirroring the shape entity_extractor reads."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = (
        payload if isinstance(payload, str) else json.dumps(payload)
    )
    return response


# ── extract_entities_from_query ──────────────────────────────────────────────


def test_query_happy_path_returns_entities():
    response = _completion(
        {"named_entities": ["Pro plan", "Acme CRM", "FooChat"]}
    )
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        result = extract_entities_from_query(
            "How much is the Pro plan in Acme CRM with FooChat?",
            "encrypted-key",
        )
    assert result == ["Pro plan", "Acme CRM", "FooChat"]


def test_query_empty_short_circuits_without_llm_call():
    """Empty / whitespace input must not spend a token."""
    with patch.object(entity_extractor, "get_openai_client") as get_client, patch.object(
        entity_extractor, "call_openai_with_retry"
    ) as retry:
        assert extract_entities_from_query("", "key") == []
        assert extract_entities_from_query("   ", "key") == []
    get_client.assert_not_called()
    retry.assert_not_called()


def test_query_missing_api_key_returns_empty():
    with patch.object(entity_extractor, "get_openai_client") as get_client:
        assert extract_entities_from_query("anything", None) == []
        assert extract_entities_from_query("anything", "") == []
    get_client.assert_not_called()


def test_query_openai_call_failure_returns_empty():
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor,
        "call_openai_with_retry",
        side_effect=RuntimeError("boom"),
    ):
        assert extract_entities_from_query("hello?", "key") == []


def test_query_client_init_failure_returns_empty():
    with patch.object(
        entity_extractor,
        "get_openai_client",
        side_effect=RuntimeError("decrypt failed"),
    ):
        assert extract_entities_from_query("hello?", "key") == []


def test_query_invalid_json_returns_empty():
    response = _completion("not valid json {")
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("hello?", "key") == []


def test_query_missing_key_in_payload_returns_empty():
    response = _completion({"something_else": ["x", "y"]})
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("hello?", "key") == []


def test_query_non_list_named_entities_returns_empty():
    response = _completion({"named_entities": "Pro plan"})  # string, not list
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("hello?", "key") == []


def test_query_non_string_items_are_dropped():
    response = _completion(
        {"named_entities": ["Pro plan", 42, None, {"x": 1}, "FooChat"]}
    )
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("hello?", "key") == ["Pro plan", "FooChat"]


def test_query_whitespace_and_duplicates_normalized():
    response = _completion(
        {"named_entities": ["  Pro plan  ", "Pro plan", "", "  ", "Acme CRM"]}
    )
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        # Order preserved, dedup case-sensitive, whitespace stripped.
        assert extract_entities_from_query("hello?", "key") == ["Pro plan", "Acme CRM"]


def test_query_case_sensitive_dedup_keeps_distinct_casing():
    """'Pro' and 'pro' may legitimately mean different things — keep both."""
    response = _completion({"named_entities": ["Pro", "pro", "PRO"]})
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("hello?", "key") == ["Pro", "pro", "PRO"]


def test_query_passes_telemetry_attribution():
    """tenant_id / bot_id must be threaded through to the retry helper."""
    response = _completion({"named_entities": []})
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ) as retry:
        extract_entities_from_query(
            "hello?", "key", tenant_id="tenant-1", bot_id="bot-7"
        )
    _, kwargs = retry.call_args
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["bot_id"] == "bot-7"


def test_query_uses_configured_model_and_json_response_format(monkeypatch):
    """Sanity check that we ask OpenAI for json_object output and the right model."""
    monkeypatch.setattr(entity_extractor.settings, "ner_model", "gpt-test-model")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _completion(
        {"named_entities": ["Acme"]}
    )

    captured: dict = {}

    def fake_retry(operation, fn, **kwargs):  # noqa: ARG001
        # Invoke the lambda — it calls fake_client.chat.completions.create
        # which records its kwargs for inspection.
        result = fn()
        captured.update(fake_client.chat.completions.create.call_args.kwargs)
        return result

    with patch.object(
        entity_extractor, "get_openai_client", return_value=fake_client
    ), patch.object(entity_extractor, "call_openai_with_retry", side_effect=fake_retry):
        out = extract_entities_from_query("какой Pro план?", "key")

    assert out == ["Acme"]
    assert captured["model"] == "gpt-test-model"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0


def test_query_passes_client_timeout_matching_wall_clock_budget(monkeypatch):
    """Codex P2 fix: transport-layer timeout must be bound to the wall-clock budget.

    Otherwise a slow OpenAI response leaves the worker thread + HTTP socket
    pegged for the default 60s client read timeout even after _run_with_timeout
    returns []. Under sustained traffic that leaks threads/sockets.
    """
    monkeypatch.setattr(entity_extractor.settings, "ner_query_timeout_seconds", 1.5)
    response = _completion({"named_entities": ["Acme"]})
    captured: dict = {}

    def fake_get_client(_key, *, timeout=None):
        captured["timeout"] = timeout
        return MagicMock()

    with patch.object(
        entity_extractor, "get_openai_client", side_effect=fake_get_client
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        extract_entities_from_query("hello?", "key")

    assert captured["timeout"] == pytest.approx(1.5)


def test_passage_does_not_constrain_client_timeout():
    """Indexing-time path keeps the default OpenAI client read timeout."""
    response = _completion({"named_entities": ["Acme"]})
    captured: dict = {}

    def fake_get_client(_key, *, timeout=None):
        captured["timeout"] = timeout
        return MagicMock()

    with patch.object(
        entity_extractor, "get_openai_client", side_effect=fake_get_client
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        extract_entities_from_passage("ipsum", "key")

    assert captured["timeout"] is None


def test_query_timeout_returns_empty(monkeypatch):
    """A slow LLM call must not exceed ner_query_timeout_seconds."""
    monkeypatch.setattr(entity_extractor.settings, "ner_query_timeout_seconds", 0.05)

    def slow_retry(*_args, **_kwargs):
        time.sleep(0.5)  # well past the 50ms budget
        return _completion({"named_entities": ["Acme"]})

    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(entity_extractor, "call_openai_with_retry", side_effect=slow_retry):
        started = time.monotonic()
        result = extract_entities_from_query("hello?", "key")
        elapsed = time.monotonic() - started

    assert result == []
    # Should bail out close to the configured budget, not wait for the full sleep.
    assert elapsed < 0.4


# ── extract_entities_from_passage ────────────────────────────────────────────


def test_passage_happy_path_returns_entities():
    response = _completion(
        {
            "named_entities": [
                "Pro plan",
                "Acme CRM",
                "$59 per month",
                "March 1, 2024",
            ]
        }
    )
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        result = extract_entities_from_passage(
            "The Pro plan in Acme CRM costs $59 per month. Launched March 1, 2024.",
            "encrypted-key",
        )
    assert result == ["Pro plan", "Acme CRM", "$59 per month", "March 1, 2024"]


def test_passage_empty_short_circuits_without_llm_call():
    with patch.object(entity_extractor, "get_openai_client") as get_client:
        assert extract_entities_from_passage("", "key") == []
        assert extract_entities_from_passage("   \n", "key") == []
    get_client.assert_not_called()


def test_passage_missing_api_key_returns_empty():
    assert extract_entities_from_passage("some text", None) == []
    assert extract_entities_from_passage("some text", "") == []


def test_passage_openai_failure_returns_empty():
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor,
        "call_openai_with_retry",
        side_effect=RuntimeError("rate limit exhausted"),
    ):
        assert extract_entities_from_passage("anything goes here", "key") == []


def test_passage_no_timeout_unlike_query(monkeypatch):
    """Passage path is indexing-time and must NOT enforce ner_query_timeout_seconds."""
    monkeypatch.setattr(entity_extractor.settings, "ner_query_timeout_seconds", 0.01)

    def slow_retry(*_args, **_kwargs):
        time.sleep(0.1)
        return _completion({"named_entities": ["Acme"]})

    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(entity_extractor, "call_openai_with_retry", side_effect=slow_retry):
        # Sleep > the (tiny) query timeout. Passage path ignores it and waits.
        result = extract_entities_from_passage("ipsum", "key")
    assert result == ["Acme"]


def test_passage_json_garbage_returns_empty():
    response = _completion("definitely not json")
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_passage("ipsum", "key") == []


def test_passage_response_missing_choices_returns_empty():
    bad = MagicMock()
    bad.choices = []  # malformed
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=bad
    ):
        assert extract_entities_from_passage("ipsum", "key") == []


def test_passage_preserves_source_language_surface_form():
    """Russian / mixed-language entities flow through verbatim."""
    response = _completion(
        {"named_entities": ["Pro план", "ошибка 429", "Acme CRM"]}
    )
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        result = extract_entities_from_passage("какой-то текст", "key")
    assert result == ["Pro план", "ошибка 429", "Acme CRM"]


# ── _parse_entities (covered indirectly above; one direct edge case) ─────────


def test_parse_entities_handles_top_level_list_payload():
    """If model ignores the JSON-object instruction and returns a top-level list,
    we treat it as malformed (named_entities key missing) and return []."""
    response = _completion('["Pro plan", "Acme CRM"]')
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("hello?", "key") == []


# ── parametrized smoke-style coverage ────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_payload,expected",
    [
        ({"named_entities": []}, []),
        ({"named_entities": ["a"]}, ["a"]),
        ({"named_entities": ["a", "a", "a"]}, ["a"]),
        ({"named_entities": ["a", "b", "a", "c"]}, ["a", "b", "c"]),
    ],
)
def test_query_payload_variants(raw_payload, expected):
    response = _completion(raw_payload)
    with patch.object(
        entity_extractor, "get_openai_client", return_value=MagicMock()
    ), patch.object(
        entity_extractor, "call_openai_with_retry", return_value=response
    ):
        assert extract_entities_from_query("q", "key") == expected
