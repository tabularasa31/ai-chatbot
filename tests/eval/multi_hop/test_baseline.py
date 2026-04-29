"""Baseline retrieval metrics on the multi-hop eval set.

Step 3 (baseline) and Step 5 (with entity-overlap channel) of the
entity-aware retrieval epic (ClickUp 86exe5pjx). Runs the 30-case
dataset through hybrid retrieval (pgvector + BM25 + RRF [+ optional
entity-overlap]) and prints recall@5 / MRR / precision@5 — overall and
per category.

This file is deliberately ONE pytest test that runs all 30 queries in a
single PG database lifecycle. Spinning up a disposable PG database per
query would be ~30x slower for no extra signal.

The assertion floor is intentionally loose. We only fail if retrieval is
catastrophically broken (overall recall@5 below 30%); the point of the
harness is to *report* numbers for before/after comparison, not to gate
CI on a specific value. Tighten thresholds in Step 6 once the entity
channel is wired and we have an after-state to anchor against.

Run:
    make multi-hop-eval        # docker up + this test, prints baseline
    pytest -m pgvector tests/eval/multi_hop/ -s  # if PG already up
"""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from backend.search.service import search_similar_chunks
from tests.eval.multi_hop import dataset as ds
from tests.eval.multi_hop.metrics import (
    aggregate,
    evaluate_case,
    format_report,
)


def _run_eval(
    *,
    tenant_id: uuid.UUID,
    uuid_to_chunk_id: dict[uuid.UUID, str],
    db: Session,
    k: int,
    ranking_depth: int,
):
    """Run all 30 queries through search_similar_chunks; return list of CaseResult."""
    cases = []
    for query in ds.QUERIES:
        results = search_similar_chunks(
            tenant_id=tenant_id,
            query=query.text,
            top_k=ranking_depth,
            db=db,
            api_key="sk-test-encrypted-noop",
        )
        ranking_chunk_ids = [
            uuid_to_chunk_id[emb.id]
            for emb, _score in results
            if emb.id in uuid_to_chunk_id
        ]
        cases.append(
            evaluate_case(
                query_id=query.query_id,
                category=query.category,
                gold=query.gold_chunk_ids,
                ranking=ranking_chunk_ids,
                k=k,
            )
        )
    return cases

K = 5
RANKING_DEPTH = 10  # How many results to ask the retriever for; MRR uses full depth.


@pytest.mark.pgvector
def test_multi_hop_baseline(
    pg_db_session: Session,
    indexed_corpus: dict,
    capsys: pytest.CaptureFixture,
) -> None:
    tenant_id = cast(uuid.UUID, indexed_corpus["tenant_id"])
    uuid_to_chunk_id: dict[uuid.UUID, str] = indexed_corpus["uuid_to_chunk_id"]

    cases = _run_eval(
        tenant_id=tenant_id,
        uuid_to_chunk_id=uuid_to_chunk_id,
        db=pg_db_session,
        k=K,
        ranking_depth=RANKING_DEPTH,
    )

    report = aggregate(cases, k=K)
    rendered = format_report(report, header="Multi-hop eval baseline (Step 3)")
    # Always print: pytest with -s shows it live; without -s it surfaces on failure.
    print("\n" + rendered)

    # Loose floor — see module docstring. Overall recall@5 below 0.3 means
    # something is structurally wrong with retrieval, not just a perf gap.
    assert report.overall.recall_at_k >= 0.3, (
        f"Overall recall@{K} = {report.overall.recall_at_k:.3f} is implausibly "
        f"low; the harness or corpus has a bug. Full report:\n{rendered}"
    )

    # Sanity: per-category report should exist for every declared category.
    for cat in ds.CATEGORIES:
        assert cat in report.per_category, f"Missing category in report: {cat}"

    # Force a final summary so the user can `grep BASELINE` in pytest output.
    print(
        "\nBASELINE_SUMMARY "
        f"recall@{K}={report.overall.recall_at_k:.3f} "
        f"mrr={report.overall.mrr:.3f} "
        f"precision@{K}={report.overall.precision_at_k:.3f}"
    )

    # Touch the captured-output API so capsys doesn't get GC'd before pytest
    # picks up the print statements (defensive — a no-op in practice).
    _ = capsys.readouterr() if False else None


@pytest.mark.pgvector
def test_multi_hop_with_entity_overlap_channel(
    pg_db_session: Session,
    indexed_corpus: dict,
    query_entities_lookup: dict[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 5 deliverable: rerun the same eval with the entity channel ON.

    Compares against the same numbers test_multi_hop_baseline produces.
    The channel is gated by ``settings.entity_overlap_enabled``; the
    baseline test runs with it OFF (default), this one flips it ON.

    Both NER calls (query-side via extract_entities_from_query, chunk-side
    via extract_entities_from_passage) are stubbed from the dataset's
    ground-truth fields. The harness measures whether the *retriever
    logic* exploits a good NER signal, not how good the NER model itself
    is — that question lives outside this eval.

    Headroom expectation per the epic:
    - error_or_endpoint: should improve (rare codes / endpoints fall
      cleanly into the entity channel).
    - control_no_entities: must NOT regress (queries with empty NER →
      RRF falls through to today's two-channel formula).
    - multi_hop / brand_specific: already at recall@5 = 1.0 in baseline,
      so the bar is "hold steady".
    """
    monkeypatch.setattr("backend.core.config.settings.entity_overlap_enabled", True)

    def stub_query_ner(query: str, _api_key, *, tenant_id=None, bot_id=None):  # noqa: ARG001
        return list(query_entities_lookup.get(query, []))

    tenant_id = cast(uuid.UUID, indexed_corpus["tenant_id"])
    uuid_to_chunk_id: dict[uuid.UUID, str] = indexed_corpus["uuid_to_chunk_id"]

    with patch(
        "backend.search.service.extract_entities_from_query",
        side_effect=stub_query_ner,
    ):
        cases = _run_eval(
            tenant_id=tenant_id,
            uuid_to_chunk_id=uuid_to_chunk_id,
            db=pg_db_session,
            k=K,
            ranking_depth=RANKING_DEPTH,
        )

    report = aggregate(cases, k=K)
    rendered = format_report(report, header="Multi-hop eval — entity channel ON (Step 5)")
    print("\n" + rendered)
    print(
        "\nENTITY_ON_SUMMARY "
        f"recall@{K}={report.overall.recall_at_k:.3f} "
        f"mrr={report.overall.mrr:.3f} "
        f"precision@{K}={report.overall.precision_at_k:.3f}"
    )

    # ── Assertions vs the merged baseline numbers ─────────────────────────
    # These are the numbers test_multi_hop_baseline reports today
    # (synthetic embeddings, deterministic). Step 5 must not regress any
    # of them. We hard-pin the floors so a future RRF tweak can't silently
    # drop quality.
    BASELINE = {
        "overall_recall": 0.933,
        "control_no_entities_recall": 0.833,
        "multi_hop_recall": 1.000,
        "brand_specific_recall": 1.000,
        "error_or_endpoint_recall": 0.875,
    }
    overall = report.overall
    by_cat = report.per_category
    eps = 1e-6  # float tolerance — these are macro-averages, no fp drift expected
    assert overall.recall_at_k >= BASELINE["overall_recall"] - eps, (
        f"Entity channel regressed overall recall: {overall.recall_at_k:.3f} "
        f"< baseline {BASELINE['overall_recall']:.3f}"
    )
    assert by_cat["control_no_entities"].recall_at_k >= (
        BASELINE["control_no_entities_recall"] - eps
    ), (
        "Control queries (no entities) regressed — entity channel must "
        f"not hurt them. Got {by_cat['control_no_entities'].recall_at_k:.3f} "
        f"< {BASELINE['control_no_entities_recall']:.3f}."
    )
    assert by_cat["multi_hop"].recall_at_k >= BASELINE["multi_hop_recall"] - eps
    assert by_cat["brand_specific"].recall_at_k >= BASELINE["brand_specific_recall"] - eps
    assert by_cat["error_or_endpoint"].recall_at_k >= (
        BASELINE["error_or_endpoint_recall"] - eps
    )


@pytest.mark.pgvector
def test_dataset_shape_invariants() -> None:
    """Cheap structural assertions so a dataset edit can't silently skew metrics."""
    assert len(ds.QUERIES) == 30, f"Dataset must have 30 queries, got {len(ds.QUERIES)}"
    by_cat: dict[str, int] = {}
    for q in ds.QUERIES:
        by_cat[q.category] = by_cat.get(q.category, 0) + 1
    assert set(by_cat) == set(ds.CATEGORIES), (
        f"Categories mismatch: dataset has {set(by_cat)}, "
        f"declared {set(ds.CATEGORIES)}"
    )
    # Roughly balanced: no category dominates the eval.
    assert all(n >= 6 for n in by_cat.values()), (
        f"Each category must have >=6 cases for stable per-category metrics, "
        f"got {by_cat}"
    )
    # All gold ids resolvable in the corpus.
    valid_ids = {c.chunk_id for c in ds.CHUNKS}
    for q in ds.QUERIES:
        assert set(q.gold_chunk_ids).issubset(valid_ids), (
            f"Query {q.query_id!r} has unknown gold chunk ids: "
            f"{set(q.gold_chunk_ids) - valid_ids}"
        )
