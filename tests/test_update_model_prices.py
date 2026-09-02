"""Tests for the price-snapshot refresh script."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

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
