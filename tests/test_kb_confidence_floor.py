"""Unit tests for kb_confidence flooring by reliability.score and the
contradiction-detected signal that feeds the decision engine.

These tests cover the wiring between retrieval reliability and the
clarification decision engine — namely that the contradiction cap and
source_overlap cap actually reach the decision engine via
`_classify_kb_confidence`, and that the hardcoded `kb_contradiction_detected`
flag is now driven by the real reliability cap reason.
"""

from __future__ import annotations

import uuid

from backend.chat.handlers.rag import (
    RetrievalContext,
    _classify_kb_confidence,
    _floor_kb_confidence,
)
from backend.search.service import (
    ContradictionPair,
    SourceOverlapPair,
    build_reliability_assessment,
)


def _make_retrieval(
    *,
    top_score: float,
    result_count: int = 5,
    contradiction_pairs: tuple[ContradictionPair, ...] = (),
    source_overlap_detected: bool = False,
    source_overlap_pairs: tuple[SourceOverlapPair, ...] = (),
) -> RetrievalContext:
    reliability = build_reliability_assessment(
        top_score=top_score,
        result_count=result_count,
        contradiction_pairs=contradiction_pairs,
        source_overlap_detected=source_overlap_detected,
        source_overlap_pairs=source_overlap_pairs,
    )
    return RetrievalContext(
        chunk_texts=["x"] * result_count,
        document_ids=[uuid.uuid4() for _ in range(result_count)],
        scores=[top_score] * result_count,
        mode="vector",
        best_rank_score=top_score,
        best_confidence_score=top_score,
        confidence_source="vector_similarity",
        reliability=reliability,
        vector_similarities=[top_score] * result_count,
    )


def test_floor_kb_confidence_lowers_when_ceiling_is_lower() -> None:
    assert _floor_kb_confidence("high", "low") == "low"
    assert _floor_kb_confidence("high", "medium") == "medium"
    assert _floor_kb_confidence("medium", "low") == "low"


def test_floor_kb_confidence_keeps_raw_when_ceiling_is_higher_or_equal() -> None:
    assert _floor_kb_confidence("low", "high") == "low"
    assert _floor_kb_confidence("medium", "high") == "medium"
    assert _floor_kb_confidence("medium", "medium") == "medium"


def test_classify_returns_high_without_caps() -> None:
    retrieval = _make_retrieval(top_score=0.9)
    assert _classify_kb_confidence(retrieval) == "high"


def test_classify_does_not_downgrade_uncapped_high_when_reliability_score_is_only_medium() -> None:
    """Regression guard: flooring must use `reliability.cap`, not `reliability.score`.

    The reliability score uses stricter base thresholds than the classifier
    (`high` only at top_score ≥ 0.8), so a top_score of 0.5 yields raw="high"
    here but reliability.score="medium". Without a cap set, the classifier
    must keep "high" — flooring by score would silently regress decision
    routing for uncapped queries.
    """
    retrieval = _make_retrieval(top_score=0.5)
    assert retrieval.reliability.cap is None
    assert retrieval.reliability.score == "medium"
    assert _classify_kb_confidence(retrieval) == "high"


def test_contradiction_cap_lowers_classify_to_low_even_for_high_top_score() -> None:
    pairs = (
        ContradictionPair(
            chunk_a_id="a",
            chunk_b_id="b",
            basis="effective_date",
            value_a="2024-03-01",
            value_b="2025-03-01",
        ),
        ContradictionPair(
            chunk_a_id="a",
            chunk_b_id="b",
            basis="version",
            value_a="v2",
            value_b="v3",
        ),
    )
    retrieval = _make_retrieval(top_score=0.9, contradiction_pairs=pairs)

    assert retrieval.reliability.cap == "low"
    assert _classify_kb_confidence(retrieval) == "low"


def test_source_overlap_cap_lowers_classify_to_medium_for_high_top_score() -> None:
    retrieval = _make_retrieval(
        top_score=0.9,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.85),
        ),
    )

    assert retrieval.reliability.cap == "medium"
    assert _classify_kb_confidence(retrieval) == "medium"


def test_classify_returns_low_when_retrieval_is_none() -> None:
    assert _classify_kb_confidence(None) == "low"


def test_classify_returns_low_when_best_confidence_score_is_none() -> None:
    retrieval = _make_retrieval(top_score=0.9)
    retrieval = RetrievalContext(
        chunk_texts=retrieval.chunk_texts,
        document_ids=retrieval.document_ids,
        scores=retrieval.scores,
        mode=retrieval.mode,
        best_rank_score=retrieval.best_rank_score,
        best_confidence_score=None,
        confidence_source="none",
        reliability=retrieval.reliability,
        vector_similarities=retrieval.vector_similarities,
    )
    assert _classify_kb_confidence(retrieval) == "low"
