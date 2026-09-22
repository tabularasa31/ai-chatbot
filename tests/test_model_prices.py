"""Tests for the vendored OpenAI price snapshot, cost arithmetic, and the
snapshot-refresh script (``scripts/update_model_prices.py``)."""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from backend.core import config
from backend.core.config import MODEL_PRICES_PATH, settings

# ---------------------------------------------------------------------------
# Snapshot + cost arithmetic (backend.core.config)
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens", "cached_tokens"),
    [(10_000, 500, 0), (1_021, 500, 510), (7, 3, 3), (0, 0, 0)],
)
def test_cost_breakdown_sums_input_and_output(
    prompt_tokens: int, completion_tokens: int, cached_tokens: int
) -> None:
    breakdown = settings.compute_cost_breakdown(
        "gpt-5-mini", prompt_tokens, completion_tokens, cached_tokens
    )

    assert breakdown["total"] == breakdown["input"] + breakdown["output"]
    assert breakdown["total"] == settings.compute_cost_usd(
        "gpt-5-mini", prompt_tokens, completion_tokens, cached_tokens
    )


def test_unreadable_snapshot_degrades_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cost telemetry must never be able to fail a chat turn."""
    monkeypatch.setattr(config, "MODEL_PRICES_PATH", MODEL_PRICES_PATH.with_name("gone.json"))
    config._model_price_snapshot.cache_clear()

    try:
        assert settings.model_cost_rates("gpt-5-mini") == {
            "input": settings.openai_default_cost_per_1m_input_tokens,
            "output": settings.openai_default_cost_per_1m_output_tokens,
            "cached_input": settings.openai_default_cost_per_1m_input_tokens,
        }
    finally:
        monkeypatch.undo()
        config._model_price_snapshot.cache_clear()


def test_cached_tokens_cannot_exceed_prompt_tokens() -> None:
    rates = settings.model_cost_rates("gpt-5-mini")

    breakdown = settings.compute_cost_breakdown(
        "gpt-5-mini", prompt_tokens=1_000, completion_tokens=0, cached_tokens=9_000
    )

    assert breakdown["total"] == round(1_000 / 1_000_000 * rates["cached_input"], 6)


# ---------------------------------------------------------------------------
# Snapshot-refresh script (scripts/update_model_prices.py)
# ---------------------------------------------------------------------------

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "update_model_prices.py"
_spec = importlib.util.spec_from_file_location("update_model_prices", _SCRIPT)
assert _spec and _spec.loader
update_model_prices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update_model_prices)


def _upstream(**models: dict[str, float]) -> dict:
    return {
        name: {
            "input_cost_per_token": rates["input"] / 1_000_000,
            "output_cost_per_token": rates["output"] / 1_000_000,
            **(
                {"cache_read_input_token_cost": rates["cached_input"] / 1_000_000}
                if "cached_input" in rates
                else {}
            ),
        }
        for name, rates in models.items()
    }


def test_matching_prices_produce_no_changes() -> None:
    tracked = {"gpt-5-mini": {"input": 0.25, "output": 2.0, "cached_input": 0.025}}

    refreshed, changes = update_model_prices.refresh(tracked, _upstream(**tracked))

    assert refreshed == tracked
    assert changes == []


def test_moved_price_is_reported_and_applied() -> None:
    tracked = {"gpt-5-mini": {"input": 1.1, "output": 4.4, "cached_input": 0.11}}
    upstream = _upstream(**{"gpt-5-mini": {"input": 0.25, "output": 2.0, "cached_input": 0.025}})

    refreshed, changes = update_model_prices.refresh(tracked, upstream)

    assert refreshed["gpt-5-mini"] == {"input": 0.25, "output": 2.0, "cached_input": 0.025}
    assert any("snapshot 1.1 vs upstream 0.25" in change for change in changes)


def test_model_missing_upstream_is_dropped_so_check_and_write_agree() -> None:
    """A retired model kept at a stale rate would leave --check red with
    nothing a refresh run could fix."""
    tracked = {
        "gpt-5-mini": {"input": 0.25, "output": 2.0, "cached_input": 0.025},
        "o1-mini": {"input": 3.0, "output": 12.0, "cached_input": 1.5},
    }
    upstream = _upstream(**{"gpt-5-mini": tracked["gpt-5-mini"]})

    refreshed, changes = update_model_prices.refresh(tracked, upstream)

    assert "o1-mini" not in refreshed
    assert any("o1-mini: dropped" in change for change in changes)


def test_missing_cached_rate_keeps_ours_instead_of_the_full_input_rate() -> None:
    tracked = {"gpt-5-mini": {"input": 0.25, "output": 2.0, "cached_input": 0.025}}
    upstream = _upstream(**{"gpt-5-mini": {"input": 0.25, "output": 2.0}})

    refreshed, changes = update_model_prices.refresh(tracked, upstream)

    assert refreshed["gpt-5-mini"]["cached_input"] == 0.025
    assert any("cached-read price" in change for change in changes)


def test_mangled_upstream_is_refused_rather_than_emptying_the_snapshot() -> None:
    tracked = {
        "gpt-5-mini": {"input": 0.25, "output": 2.0, "cached_input": 0.025},
        "gpt-4o": {"input": 2.5, "output": 10.0, "cached_input": 1.25},
        "o3": {"input": 2.0, "output": 8.0, "cached_input": 0.5},
    }

    with pytest.raises(update_model_prices.UpstreamUnusable):
        update_model_prices.refresh(tracked, {"gpt-5-mini": "not-a-dict"})


def test_the_shipped_snapshot_survives_a_refresh_against_itself() -> None:
    tracked = update_model_prices._load_snapshot()["models"]

    refreshed, changes = update_model_prices.refresh(tracked, _upstream(**tracked))

    assert refreshed == tracked
    assert changes == []
