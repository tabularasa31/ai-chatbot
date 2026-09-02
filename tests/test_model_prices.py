"""Tests for the vendored OpenAI price snapshot and cost arithmetic."""

from __future__ import annotations

import json

from backend.core.config import MODEL_PRICES_PATH, settings


def test_snapshot_entries_are_complete() -> None:
    snapshot = json.loads(MODEL_PRICES_PATH.read_text(encoding="utf-8"))

    assert snapshot["unit"] == "usd_per_1m_tokens"
    assert snapshot["models"], "snapshot must track at least one model"
    for model, rates in snapshot["models"].items():
        assert set(rates) == {"input", "output", "cached_input"}, model
        assert all(value > 0 for value in rates.values()), model
        assert rates["cached_input"] <= rates["input"], model


def test_chat_model_is_priced() -> None:
    rates = settings.model_cost_rates(settings.chat_model)

    assert rates["input"] != settings.openai_default_cost_per_1m_input_tokens
    assert rates["output"] != settings.openai_default_cost_per_1m_output_tokens


def test_dated_model_id_resolves_to_base_rates() -> None:
    assert settings.model_cost_rates("gpt-5-mini-2025-08-07") == settings.model_cost_rates(
        "gpt-5-mini"
    )


def test_unknown_model_falls_back_to_defaults() -> None:
    rates = settings.model_cost_rates("some-model-we-never-heard-of")

    assert rates == {
        "input": settings.openai_default_cost_per_1m_input_tokens,
        "output": settings.openai_default_cost_per_1m_output_tokens,
        "cached_input": settings.openai_default_cost_per_1m_input_tokens,
    }


def test_cached_prompt_tokens_are_billed_at_the_cached_rate() -> None:
    rates = settings.model_cost_rates("gpt-5-mini")

    breakdown = settings.compute_cost_breakdown(
        "gpt-5-mini", prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=800_000
    )

    assert breakdown["input"] == round(
        0.2 * rates["input"] + 0.8 * rates["cached_input"], 6
    )
    assert breakdown["output"] == 0.0
    assert breakdown["total"] == breakdown["input"]


def test_cost_breakdown_sums_input_and_output() -> None:
    breakdown = settings.compute_cost_breakdown(
        "gpt-5-mini", prompt_tokens=10_000, completion_tokens=500
    )

    assert breakdown["total"] == round(breakdown["input"] + breakdown["output"], 6)
    assert breakdown["total"] == settings.compute_cost_usd("gpt-5-mini", 10_000, 500)


def test_cached_tokens_cannot_exceed_prompt_tokens() -> None:
    rates = settings.model_cost_rates("gpt-5-mini")

    breakdown = settings.compute_cost_breakdown(
        "gpt-5-mini", prompt_tokens=1_000, completion_tokens=0, cached_tokens=9_000
    )

    assert breakdown["total"] == round(1_000 / 1_000_000 * rates["cached_input"], 6)
