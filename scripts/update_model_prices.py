#!/usr/bin/env python
"""Refresh (or verify) the vendored OpenAI price snapshot.

Token prices are not discoverable at runtime — OpenAI's ``/v1/models`` returns
no pricing and the published price list is an HTML page. The rates therefore
live in ``backend/core/model_prices.json``, and the only thing standing between
them and silent rot is this script: LiteLLM's community price file is the
upstream, a weekly workflow runs the refresh and opens a pull request when the
rates moved, and ``--check`` guards hand edits to the snapshot. Before it
existed, hand-typed rates went stale unnoticed for months — gpt-5-mini was
billed at o3-mini's price and o3 at five times its own.

The file is rewritten only when a rate actually changed, so the weekly run is a
no-op most weeks instead of a pull request that moves a timestamp.

Network failures are not drift: both modes skip rather than failing when
upstream is unreachable, so an outage at GitHub cannot redden an unrelated PR.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
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


def _load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _fetch_upstream() -> dict:
    request = urllib.request.Request(UPSTREAM_URL, headers={"User-Agent": "chat9-price-check"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _upstream_rates(entry: dict) -> dict[str, float]:
    rates: dict[str, float] = {}
    for name, field in UPSTREAM_FIELDS.items():
        value = entry.get(field)
        if value is None:
            continue
        rates[name] = round(float(value) * PER_MILLION, 6)
    return rates


def _resolve(upstream: dict, model: str) -> tuple[dict[str, float] | None, str | None]:
    entry = upstream.get(model)
    if entry is None:
        return None, f"{model}: missing upstream (deprecated or renamed?)"
    rates = _upstream_rates(entry)
    for required in ("input", "output"):
        if required not in rates:
            return None, f"{model}: upstream has no {required} price"
    rates.setdefault("cached_input", rates["input"])
    return rates, None


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
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"warning: upstream price list unreachable ({exc}) — skipping", file=sys.stderr)
        return 0

    problems: list[str] = []
    refreshed: dict[str, dict[str, float]] = {}
    for model, current in sorted(tracked.items()):
        rates, problem = _resolve(upstream, model)
        if rates is None:
            problems.append(problem or f"{model}: unusable upstream entry")
            refreshed[model] = current
            continue
        refreshed[model] = rates
        for name, value in rates.items():
            if current.get(name) != value:
                problems.append(
                    f"{model}.{name}: snapshot {current.get(name)} vs upstream {value} USD/1M"
                )

    if args.check:
        if problems:
            print("Model price snapshot is out of date:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print(
                "\nRun `python scripts/update_model_prices.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"Model price snapshot matches upstream ({len(tracked)} models).")
        return 0

    if refreshed == tracked:
        print(f"Model price snapshot matches upstream ({len(tracked)} models).")
        return 0

    snapshot["models"] = refreshed
    snapshot["refreshed_at"] = dt.date.today().isoformat()
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for problem in problems:
        print(f"  - {problem}")
    print(f"Wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)} ({len(refreshed)} models).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
