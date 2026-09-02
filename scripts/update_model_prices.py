#!/usr/bin/env python
"""Refresh (or verify) the vendored OpenAI price snapshot.

Token prices are not discoverable at runtime: ``/v1/models`` carries none and
the published list is an HTML page. The rates therefore live in
``backend/core/model_prices.json``, refreshed from LiteLLM's community price
file by this script and verified against it in CI.

Both modes derive the same refreshed snapshot, so ``--check`` fails exactly
when a refresh run would write something — a drift the check reports can
always be resolved by running the script.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "backend" / "core" / "model_prices.json"
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
TIMEOUT_SECONDS = 60

#: Snapshot rates are USD per 1M tokens; upstream quotes USD per token.
PER_MILLION = 1_000_000
UPSTREAM_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cached_input": "cache_read_input_token_cost",
}


class UpstreamUnusable(Exception):
    """The price file was fetched but cannot be trusted as a whole."""


def _load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _fetch_upstream() -> dict | None:
    """Return the upstream price table, or ``None`` when it is unreachable.

    A transient outage must not fail a run; a 4xx must, since it means the
    file moved and every later run would otherwise skip in silence.
    """
    request = urllib.request.Request(UPSTREAM_URL, headers={"User-Agent": "chat9-price-check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            raise UpstreamUnusable(f"{UPSTREAM_URL} returned HTTP {exc.code}") from exc
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _upstream_rates(entry: object) -> dict[str, float]:
    if not isinstance(entry, dict):
        return {}
    rates: dict[str, float] = {}
    for name, field in UPSTREAM_FIELDS.items():
        value = entry.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        rates[name] = round(float(value) * PER_MILLION, 6)
    return rates


def refresh(tracked: dict[str, dict], upstream: dict) -> tuple[dict[str, dict], list[str]]:
    """Return the snapshot upstream implies, plus a line per difference.

    A model upstream no longer prices is dropped rather than kept at a stale
    rate: keeping it would leave ``--check`` red with nothing a refresh run
    could fix. Dropped models fall back to the default rates in config.
    """
    refreshed: dict[str, dict[str, float]] = {}
    changes: list[str] = []

    for model, current in sorted(tracked.items()):
        rates = _upstream_rates(upstream.get(model))
        if "input" not in rates or "output" not in rates:
            changes.append(f"{model}: dropped — no longer priced upstream")
            continue
        if "cached_input" not in rates:
            rates["cached_input"] = current.get("cached_input", rates["input"])
            changes.append(f"{model}: upstream quotes no cached-read price, keeping ours")
        refreshed[model] = rates
        for field, value in rates.items():
            if current.get(field) != value:
                changes.append(
                    f"{model}.{field}: snapshot {current.get(field)} vs upstream {value} USD/1M"
                )

    if tracked and len(refreshed) < math.ceil(len(tracked) / 2):
        raise UpstreamUnusable(
            f"only {len(refreshed)} of {len(tracked)} tracked models are priced upstream"
        )
    return refreshed, changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift and exit non-zero instead of rewriting the snapshot.",
    )
    args = parser.parse_args()

    snapshot = _load_snapshot()
    tracked = snapshot["models"]

    try:
        upstream = _fetch_upstream()
        if upstream is None:
            print("warning: upstream price list unreachable — skipping", file=sys.stderr)
            return 0
        refreshed, changes = refresh(tracked, upstream)
    except UpstreamUnusable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if refreshed == tracked:
        print(f"Model price snapshot matches upstream ({len(tracked)} models).")
        return 0

    if args.check:
        print("Model price snapshot is out of date:", file=sys.stderr)
        for change in changes:
            print(f"  - {change}", file=sys.stderr)
        print(
            "\nRun `python scripts/update_model_prices.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    snapshot["models"] = refreshed
    snapshot["refreshed_at"] = dt.date.today().isoformat()
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for change in changes:
        print(f"  - {change}")
    print(f"Wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)} ({len(refreshed)} models).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
