"""Baseline retrieval metrics on the multi-hop eval set.

Step 3 of the entity-aware retrieval epic (ClickUp 86exe5pjx). Runs the
30-case dataset through today's hybrid retriever (pgvector + BM25 + RRF)
and prints recall@5 / MRR / precision@5 — overall and per category.

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

import pytest
from sqlalchemy.orm import Session

from backend.search.service import search_similar_chunks
from tests.eval.multi_hop import dataset as ds
from tests.eval.multi_hop.metrics import (
    aggregate,
    evaluate_case,
    format_report,
)

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

    cases = []
    for query in ds.QUERIES:
        results = search_similar_chunks(
            tenant_id=tenant_id,
            query=query.text,
            top_k=RANKING_DEPTH,
            db=pg_db_session,
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
                k=K,
            )
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
