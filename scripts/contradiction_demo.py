"""End-to-end smoke for the contradiction-adjudication cap-suppression feature.

Runs the production code path in isolation:
  detector → effective_pairs → real OpenAI adjudication → cap decision.

Reads `OPENAI_API_KEY` and the contradiction-adjudication settings from your
local `.env` via `backend.core.config`. No DB, no Langfuse, no HTTP — just the
search-layer functions exercised with real metadata pairs and a real LLM call.

Usage::

    python scripts/contradiction_demo.py            # runs both flag states
    python scripts/contradiction_demo.py --on-only  # just flag=true
    python scripts/contradiction_demo.py --off-only # just flag=false

The script prints, for each flag state:
  - the detector's effective_pairs (what would feed the arbiter)
  - the real LLM verdict per fact + run status / counts
  - the resulting cap, cap_reason and reliability score

Cost: one OpenAI request to `CONTRADICTION_ADJUDICATION_MODEL` per flag state
(~$0.005 with gpt-4.1-mini at default token budget).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow `python scripts/contradiction_demo.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Generate an in-process Fernet key when one is not provided. The script needs
# one because the production codepath decrypts the OpenAI key from a Tenant row
# shape. Setting it at import time keeps `decrypt_value` happy without changing
# any backend code.
if not os.environ.get("ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet  # type: ignore[import-not-found]

    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from backend.core.config import settings  # noqa: E402
from backend.core.crypto import encrypt_value  # noqa: E402
from backend.search.contradiction_adjudication import (  # noqa: E402
    ContradictionAdjudication,
    ContradictionAdjudicationCandidate,
    FactAdjudicationResult,
    adjudicate_contradictions,
    build_contradiction_adjudication_run,
    serialize_contradiction_adjudication_run,
)
from backend.search.service import (  # noqa: E402
    AdjudicatedContradiction,
    ContradictionAdjudicationEvidence,
    ContradictionPair,
    build_reliability_assessment,
)


PAIR_A = ContradictionPair(
    chunk_a_id="demo-chunk-a",
    chunk_b_id="demo-chunk-b",
    basis="effective_date",
    value_a="2024-03-01",
    value_b="2025-03-01",
)
PAIR_B = ContradictionPair(
    chunk_a_id="demo-chunk-a",
    chunk_b_id="demo-chunk-b",
    basis="version",
    value_a="v1",
    value_b="v2",
)
DEMO_PAIRS = (PAIR_A, PAIR_B)

# Two passages that both talk about the same shipping policy but disagree on
# both effective_date and version. A reasonable arbiter will likely return
# `confirmed` (real conflict) — which is the more common case in practice and
# the one we want to confirm fail-open works for.
CHUNK_A_TEXT = (
    "Срок доставки заказа — 3 рабочих дня. "
    "Действует с 1 марта 2024. Версия политики v1."
)
CHUNK_B_TEXT = (
    "Срок доставки заказа — 7 рабочих дней. "
    "Действует с 1 марта 2025. Версия политики v2."
)


def _build_candidates() -> list[ContradictionAdjudicationCandidate]:
    return [
        ContradictionAdjudicationCandidate(
            fact_id=f"fact_{idx + 1:03d}",
            chunk_a_id=pair.chunk_a_id,
            chunk_b_id=pair.chunk_b_id,
            basis=pair.basis,
            value_a=pair.value_a,
            value_b=pair.value_b,
            preview_a=CHUNK_A_TEXT,
            preview_b=CHUNK_B_TEXT,
            metadata_a={"chunk_index": 0, "basis_value": pair.value_a},
            metadata_b={"chunk_index": 1, "basis_value": pair.value_b},
        )
        for idx, pair in enumerate(DEMO_PAIRS)
    ]


def _evidence_from_run(run, pairs):
    by_id = {item.fact_id: item.adjudication for item in run.items}
    items = []
    for idx, pair in enumerate(pairs, start=1):
        fact_id = f"fact_{idx:03d}"
        items.append(
            AdjudicatedContradiction(
                fact_id=fact_id, pair=pair, adjudication=by_id.get(fact_id)
            )
        )
    return ContradictionAdjudicationEvidence(run=run, items=tuple(items))


def _build_mock_rejected_run() -> object:
    """Synthesize a 'completed' run with verdict='rejected' for every fact.

    Skips the real OpenAI call; useful when only validating the cap-suppression
    branch and avoiding cost / external dependencies.
    """
    items = tuple(
        FactAdjudicationResult(
            fact_id=f"fact_{idx + 1:03d}",
            adjudication=ContradictionAdjudication(
                verdict="rejected",
                rationale="(mocked) different documents, not a real contradiction",
                model="mock",
            ),
        )
        for idx in range(len(DEMO_PAIRS))
    )
    return build_contradiction_adjudication_run(
        enabled=True,
        applied_to_any_fact=True,
        status="completed",
        candidate_count=len(DEMO_PAIRS),
        sent_count=len(DEMO_PAIRS),
        completed_count=len(DEMO_PAIRS),
        rejected_count=len(DEMO_PAIRS),
        model="mock",
        items=items,
    )


def _run_once(*, flag_value: bool, mock_rejected: bool) -> None:
    print(f"\n========== flag CONTRADICTION_ADJUDICATION_FILTER_CAP_ENABLED={flag_value} ==========")

    if mock_rejected:
        run = _build_mock_rejected_run()
        print("  arbiter run: (mocked) all facts → verdict='rejected'")
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            sys.exit("OPENAI_API_KEY must be set in the environment (or .env).")
        candidates = _build_candidates()
        # The pipeline expects the same Fernet-encrypted shape that lives on
        # Tenant rows. Local .env carries a plaintext OPENAI_API_KEY, so
        # encrypt it here using the same helper so `get_openai_client` can
        # decrypt successfully.
        encrypted_key = encrypt_value(api_key)
        run = adjudicate_contradictions(
            candidates,
            api_key=encrypted_key,
            model=settings.contradiction_adjudication_model,
            max_facts=settings.contradiction_adjudication_max_facts,
            preview_chars=settings.contradiction_adjudication_preview_chars,
            max_completion_tokens=settings.contradiction_adjudication_max_tokens,
        )
    payload = serialize_contradiction_adjudication_run(run)
    if not mock_rejected:
        print(
            "  arbiter run:",
            {
                "status": payload["status"],
                "sent_count": payload["sent_count"],
                "confirmed": payload["confirmed_count"],
                "rejected": payload["rejected_count"],
                "inconclusive": payload["inconclusive_count"],
                "errors": payload["error_count"],
            },
        )
    pair_basis_by_fact_id = {
        f"fact_{idx + 1:03d}": pair.basis for idx, pair in enumerate(DEMO_PAIRS)
    }
    for item in payload["items"]:
        adj = item["adjudication"] or {}
        verdict = adj.get("verdict") or adj.get("skip_reason") or adj.get("error") or "missing"
        rationale = (adj.get("rationale") or "").strip()
        basis = pair_basis_by_fact_id.get(item["fact_id"], "?")
        # Trim long error messages so the per-fact line stays readable.
        verdict_short = verdict if len(verdict) <= 60 else verdict[:57] + "…"
        print(f"    - {item['fact_id']} ({basis}): {verdict_short}  — {rationale[:120]}")

    evidence = _evidence_from_run(run, DEMO_PAIRS)

    # Build the reliability object under the chosen flag value.
    original = settings.contradiction_adjudication_filter_cap_enabled
    try:
        settings.contradiction_adjudication_filter_cap_enabled = flag_value
        reliability = build_reliability_assessment(
            top_score=0.9,
            result_count=5,
            contradiction_pairs=DEMO_PAIRS,
            contradiction_adjudication=evidence,
        )
    finally:
        settings.contradiction_adjudication_filter_cap_enabled = original

    print("  reliability:", {
        "base_score": reliability.base_score,
        "score": reliability.score,
        "cap": reliability.cap,
        "cap_reason": reliability.cap_reason,
    })

    suppressed = reliability.cap is None and reliability.cap_reason is None
    if flag_value and suppressed:
        verdict_summary = "✅ cap dropped (all facts rejected, suppression engaged)"
    elif flag_value and not suppressed:
        verdict_summary = (
            "🟡 cap kept (fail-open: at least one verdict was not 'rejected', "
            "or run did not complete cleanly)"
        )
    elif not flag_value and not suppressed:
        verdict_summary = "✅ legacy behaviour (cap kept, flag off)"
    else:
        verdict_summary = "❌ unexpected: cap dropped with flag off"
    print("  verdict:    ", verdict_summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--on-only", action="store_true")
    group.add_argument("--off-only", action="store_true")
    parser.add_argument(
        "--mock-all-rejected",
        action="store_true",
        help=(
            "Skip the real OpenAI call and synthesize a completed run with "
            "verdict='rejected' for every fact. Use this to exercise the "
            "cap-suppression branch without depending on a live OpenAI key."
        ),
    )
    args = parser.parse_args()

    if args.on_only:
        states = [True]
    elif args.off_only:
        states = [False]
    else:
        states = [False, True]

    for state in states:
        _run_once(flag_value=state, mock_rejected=args.mock_all_rejected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
