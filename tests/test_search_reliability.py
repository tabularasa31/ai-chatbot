"""Unit tests for contradiction detection, reliability-score
assessment/projection, and the contradiction-adjudication layer.
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from backend.search.contradiction_adjudication import (
    ContradictionAdjudication,
    ContradictionAdjudicationCandidate,
    FactAdjudicationResult,
    adjudicate_contradictions,
    build_contradiction_adjudication_run,
)
from backend.search.service import (
    AdjudicatedContradiction,
    ContradictionPair,
    ContradictionAdjudicationEvidence,
    SourceOverlapPair,
    build_reliability_assessment,
    build_reliability_projection,
    detect_metadata_contradictions,
    detect_source_overlaps,
    serialize_reliability,
)


@pytest.mark.parametrize(
    "same_document, expected_detected",
    [
        pytest.param(False, True, id="cross_document_overlap_over_threshold_detected"),
        pytest.param(True, False, id="same_document_pair_ignored"),
    ],
)
def test_detect_source_overlaps_document_gating(same_document: bool, expected_detected: bool) -> None:
    from backend.models import Embedding

    document_a = uuid.uuid4()
    document_b = document_a if same_document else uuid.uuid4()
    first = Embedding(
        id=uuid.uuid4(),
        document_id=document_a,
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=document_b,
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1},
    )

    detected, pairs = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=0.6,
    )

    assert detected is expected_detected
    if expected_detected:
        assert pairs == (
            SourceOverlapPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                similarity=0.8333,
            ),
        )
    else:
        assert pairs == ()


@pytest.mark.parametrize(
    "similarity_threshold, expected_detected",
    [
        pytest.param(0.75, True, id="at_threshold_boundary_detected"),
        pytest.param(0.76, False, id="above_threshold_boundary_not_detected"),
    ],
)
def test_detect_source_overlaps_respects_similarity_threshold_boundary(
    similarity_threshold: float, expected_detected: bool
) -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="alpha beta gamma",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="alpha beta gamma delta",
        metadata_json={"chunk_index": 1},
    )

    detected, _pairs = detect_source_overlaps(
        [(first, 0.9), (second, 0.88)],
        similarity_threshold=similarity_threshold,
    )

    assert detected is expected_detected


def test_build_reliability_assessment_uses_overlap_signal_without_conflict_semantics() -> None:
    overlap_pair = SourceOverlapPair(
        chunk_a_id="a",
        chunk_b_id="b",
        similarity=0.88,
    )

    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(overlap_pair,),
        source_overlap_similarity_threshold=0.75,
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "medium",
        "cap": "medium",
        "cap_reason": "source_overlap",
        "signals": [{"kind": "source_overlap"}],
        "evidence": {
            "source_overlap": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "similarity": 0.88,
                        "signal_type": "cross_document_overlap",
                    }
                ],
                "similarity_threshold": 0.75,
            }
        },
    }

    projection = build_reliability_projection(reliability)
    assert projection["source_overlap_detected"] is True
    assert projection["source_overlap_pairs"] == [
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "similarity": 0.88,
            "signal_type": "cross_document_overlap",
        }
    ]
    assert projection["contradiction_detected"] is False
    assert projection["contradiction_count"] == 0
    assert projection["contradiction_pair_count"] == 0
    assert projection["contradiction_basis_types"] == []
    assert projection["reliability"]["score"] == "medium"
    assert projection["reliability"]["cap_reason"] == "source_overlap"


def test_build_reliability_assessment_no_signal_serializes_stable_empty_shape() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "high",
        "cap": None,
        "cap_reason": None,
        "signals": [],
        "evidence": {},
    }

    projection = build_reliability_projection(reliability)
    assert projection["source_overlap_detected"] is False
    assert projection["source_overlap_pairs"] == []
    assert projection["contradiction_detected"] is False
    assert projection["contradiction_count"] == 0
    assert projection["contradiction_pair_count"] == 0
    assert projection["contradiction_basis_types"] == []
    assert projection["reliability"]["score"] == "high"
    assert projection["reliability"]["cap_reason"] is None


def test_build_reliability_assessment_overlap_cap_is_not_applied_when_base_score_is_already_medium() -> None:
    reliability = build_reliability_assessment(
        top_score=0.6,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
    )

    assert reliability.base_score == "medium"
    assert reliability.score == "medium"
    assert reliability.cap is None
    assert reliability.cap_reason is None
    assert serialize_reliability(reliability)["signals"] == [{"kind": "source_overlap"}]


@pytest.mark.parametrize(
    "metadata_a, metadata_b, expected_bases",
    [
        pytest.param(
            {"effective_date": "2024-03-01"},
            {"effective_date": "2025-03-01"},
            ("effective_date",),
            id="effective_date_disagreement_flagged",
        ),
        pytest.param(
            {"effective_date": "2024-03-01"},
            {},
            (),
            id="single_sided_metadata_ignored",
        ),
        pytest.param(
            {"effective_date": "2024"},
            {"effective_date": "2024-03"},
            (),
            id="date_granularity_treated_as_compatible",
        ),
        pytest.param(
            {"version": "v2"},
            {"version": "2.0"},
            (),
            id="version_equivalence_normalized",
        ),
        pytest.param(
            {"effective_date": "2024-03-01", "version": "v2"},
            {"effective_date": "2025-03-01", "version": "v3"},
            ("effective_date", "version"),
            id="multiple_facts_emitted_for_one_overlap_pair",
        ),
    ],
)
def test_detect_metadata_contradictions_basis_matrix(
    metadata_a: dict, metadata_b: dict, expected_bases: tuple[str, ...]
) -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0, **metadata_a},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1, **metadata_b},
    )

    overlap_pairs = (
        SourceOverlapPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            similarity=0.83,
        ),
    )

    contradiction_pairs = detect_metadata_contradictions(
        [(first, 0.9), (second, 0.88)],
        overlap_pairs,
    )

    assert tuple(pair.basis for pair in contradiction_pairs) == expected_bases
    for pair in contradiction_pairs:
        assert pair.chunk_a_id == str(first.id)
        assert pair.chunk_b_id == str(second.id)


def test_build_reliability_assessment_keeps_single_contradiction_as_evidence_only() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "medium",
        "cap": "medium",
        "cap_reason": "source_overlap",
        "signals": [{"kind": "source_overlap"}, {"kind": "contradiction"}],
        "evidence": {
            "source_overlap": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "similarity": 0.81,
                        "signal_type": "cross_document_overlap",
                    }
                ],
                "similarity_threshold": 0.75,
            },
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    }
                ]
            },
        },
    }


def test_build_reliability_assessment_caps_to_low_for_multiple_facts_on_same_pair() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=(
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
        ),
    )

    assert serialize_reliability(reliability) == {
        "base_score": "high",
        "score": "low",
        "cap": "low",
        "cap_reason": "contradiction",
        "signals": [{"kind": "source_overlap"}, {"kind": "contradiction"}],
        "evidence": {
            "source_overlap": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "similarity": 0.81,
                        "signal_type": "cross_document_overlap",
                    }
                ],
                "similarity_threshold": 0.75,
            },
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    },
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "version",
                        "value_a": "v2",
                        "value_b": "v3",
                    },
                ]
            },
        },
    }


@pytest.mark.parametrize(
    "top_score, contradiction_pairs, source_overlap_pairs, expected_score, expected_cap_reason, expected_pair_count",
    [
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="revision", value_a="rev 1", value_b="rev 2"),
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="revision", value_a="rev 3", value_b="rev 4"),
            ),
            (),
            "low",
            "contradiction",
            2,
            id="distinct_same_basis_facts_cap_low",
        ),
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
                ContradictionPair(chunk_a_id="c", chunk_b_id="d", basis="version", value_a="v2", value_b="v3"),
            ),
            (),
            "low",
            "contradiction",
            2,
            id="contradictions_across_distinct_pairs_cap_low",
        ),
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
                ContradictionPair(chunk_a_id="c", chunk_b_id="d", basis="", value_a="v2", value_b="v3"),
            ),
            (),
            "high",
            None,
            1,
            id="invalid_basis_filtered_before_threshold",
        ),
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
            )
            * 2,
            (),
            "high",
            None,
            1,
            id="exact_duplicate_contradiction_deduplicated",
        ),
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
                ContradictionPair(chunk_a_id="b", chunk_b_id="a", basis="effective_date", value_a="2025-03-01", value_b="2024-03-01"),
            ),
            (),
            "high",
            None,
            1,
            id="mirrored_duplicate_contradiction_deduplicated",
        ),
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
                ContradictionPair(chunk_a_id="b", chunk_b_id="a", basis="version", value_a="v3", value_b="v2"),
            ),
            (),
            "low",
            "contradiction",
            2,
            id="mirrored_distinct_basis_facts_counted_separately",
        ),
        pytest.param(
            0.9,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
                ContradictionPair(chunk_a_id="c", chunk_b_id="d", basis="version", value_a="v2", value_b="v3"),
            ),
            (SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),),
            "low",
            "contradiction",
            2,
            id="contradiction_cap_short_circuits_overlap_cap",
        ),
        pytest.param(
            0.4,
            (
                ContradictionPair(chunk_a_id="a", chunk_b_id="b", basis="effective_date", value_a="2024-03-01", value_b="2025-03-01"),
                ContradictionPair(chunk_a_id="c", chunk_b_id="d", basis="version", value_a="v2", value_b="v3"),
            ),
            (),
            "low",
            "contradiction",
            2,
            id="keeps_contradiction_reason_when_base_score_already_low",
        ),
    ],
)
def test_build_reliability_assessment_contradiction_cap_variations(
    top_score: float | None,
    contradiction_pairs: tuple[ContradictionPair, ...],
    source_overlap_pairs: tuple[SourceOverlapPair, ...],
    expected_score: str,
    expected_cap_reason: str | None,
    expected_pair_count: int,
) -> None:
    reliability = build_reliability_assessment(
        top_score=top_score,
        result_count=5,
        source_overlap_detected=bool(source_overlap_pairs),
        source_overlap_pairs=source_overlap_pairs,
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=contradiction_pairs,
    )

    assert reliability.score == expected_score
    assert reliability.cap_reason == expected_cap_reason
    pairs = reliability.evidence.contradiction.pairs if reliability.evidence.contradiction else ()
    assert len(pairs) == expected_pair_count


def _build_adjudication_evidence(
    pairs: tuple[ContradictionPair, ...],
    verdicts: tuple[str | None, ...],
    *,
    status: str = "completed",
    sent_count: int | None = None,
    skip_reasons: tuple[str | None, ...] | None = None,
) -> ContradictionAdjudicationEvidence:
    """Build adjudication evidence for the given pairs and per-fact verdicts."""
    items: list[AdjudicatedContradiction] = []
    for index, (pair, verdict) in enumerate(zip(pairs, verdicts), start=1):
        skip_reason = (
            skip_reasons[index - 1] if skip_reasons and index - 1 < len(skip_reasons) else None
        )
        adj = ContradictionAdjudication(
            verdict=verdict,
            model="gpt-4.1-mini",
            skip_reason=skip_reason,
        )
        items.append(
            AdjudicatedContradiction(
                fact_id=f"fact_{index:03d}",
                pair=pair,
                adjudication=adj,
            )
        )
    sent = sent_count if sent_count is not None else sum(1 for v in verdicts if v is not None)
    rejected = sum(1 for v in verdicts if v == "rejected")
    confirmed = sum(1 for v in verdicts if v == "confirmed")
    inconclusive = sum(1 for v in verdicts if v == "inconclusive")
    run = build_contradiction_adjudication_run(
        enabled=True,
        status=status,
        candidate_count=len(pairs),
        sent_count=sent,
        completed_count=sent,
        confirmed_count=confirmed,
        rejected_count=rejected,
        inconclusive_count=inconclusive,
        model="gpt-4.1-mini",
        applied_to_any_fact=True,
    )
    return ContradictionAdjudicationEvidence(run=run, items=tuple(items))


def _two_facts_same_pair() -> tuple[ContradictionPair, ContradictionPair]:
    return (
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


@pytest.mark.parametrize(
    "filter_cap_enabled, verdicts, status, sent_count, skip_reasons, expected_cap_reason",
    [
        pytest.param(False, ("rejected", "rejected"), "completed", None, None, "contradiction", id="filter_disabled_keeps_cap_even_if_all_rejected"),
        pytest.param(True, ("rejected", "rejected"), "completed", None, None, None, id="filter_enabled_drops_cap_when_all_rejected"),
        pytest.param(True, ("rejected", "confirmed"), "completed", None, None, "contradiction", id="any_confirmed_verdict_keeps_cap"),
        pytest.param(True, ("rejected", "inconclusive"), "completed", None, None, "contradiction", id="any_inconclusive_verdict_keeps_cap"),
        pytest.param(True, ("rejected", "rejected"), "failed_open", None, None, "contradiction", id="failed_open_status_keeps_cap"),
        pytest.param(True, ("rejected", None), "completed", 1, (None, "fact_limit"), "contradiction", id="partial_unjudged_fact_keeps_cap"),
    ],
)
def test_adjudication_cap_suppression_matrix(
    monkeypatch: pytest.MonkeyPatch,
    filter_cap_enabled: bool,
    verdicts: tuple[str | None, ...],
    status: str,
    sent_count: int | None,
    skip_reasons: tuple[str | None, ...] | None,
    expected_cap_reason: str | None,
) -> None:
    monkeypatch.setattr(
        "backend.search.service.settings.contradiction_adjudication_filter_cap_enabled",
        filter_cap_enabled,
    )
    pairs = _two_facts_same_pair()
    evidence = _build_adjudication_evidence(
        pairs, verdicts, status=status, sent_count=sent_count, skip_reasons=skip_reasons
    )

    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=pairs,
        contradiction_adjudication=evidence,
    )

    assert reliability.cap_reason == expected_cap_reason
    if expected_cap_reason is None:
        assert reliability.cap is None
        assert reliability.score == "high"
        # Effective contradiction pairs are still surfaced as evidence/signal for traces.
        assert reliability.evidence.contradiction is not None
        assert any(signal.kind == "contradiction" for signal in reliability.signals)
    else:
        assert reliability.cap == "low"
        assert reliability.score == "low"


def test_build_reliability_projection_does_not_mutate_canonical_object() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        source_overlap_detected=True,
        source_overlap_pairs=(
            SourceOverlapPair(chunk_a_id="a", chunk_b_id="b", similarity=0.81),
        ),
        source_overlap_similarity_threshold=0.75,
    )
    before = serialize_reliability(reliability)
    projection = build_reliability_projection(reliability)

    assert projection["reliability"] == before
    assert serialize_reliability(reliability) == before


def test_build_reliability_projection_derives_contradiction_metrics_from_final_canonical_entries() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
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
            ContradictionPair(
                chunk_a_id="c",
                chunk_b_id="d",
                basis="effective_date",
                value_a="2024-04-01",
                value_b="2025-04-01",
            ),
        ),
    )

    projection = build_reliability_projection(reliability)

    assert projection["contradiction_detected"] is True
    assert projection["contradiction_count"] == 3
    assert projection["contradiction_pair_count"] == 2
    assert projection["contradiction_basis_types"] == ["effective_date", "version"]
    assert projection["reliability"]["evidence"]["contradiction"]["pairs"] == [
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "basis": "effective_date",
            "value_a": "2024-03-01",
            "value_b": "2025-03-01",
        },
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "basis": "version",
            "value_a": "v2",
            "value_b": "v3",
        },
        {
            "chunk_a_id": "c",
            "chunk_b_id": "d",
            "basis": "effective_date",
            "value_a": "2024-04-01",
            "value_b": "2025-04-01",
        },
    ]


def test_build_reliability_projection_uses_canonical_mirror_dedup_for_metrics() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(
            ContradictionPair(
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
            ),
            ContradictionPair(
                chunk_a_id="b",
                chunk_b_id="a",
                basis="effective_date",
                value_a="2025-03-01",
                value_b="2024-03-01",
            ),
        ),
    )

    projection = build_reliability_projection(reliability)

    assert projection["contradiction_detected"] is True
    assert projection["contradiction_count"] == 1
    assert projection["contradiction_pair_count"] == 1
    assert projection["contradiction_basis_types"] == ["effective_date"]


def test_build_reliability_projection_is_stable_for_empty_default_object() -> None:
    projection = build_reliability_projection(
        build_reliability_assessment(top_score=None, result_count=0)
    )

    assert projection["reliability"]["signals"] == [{"kind": "weak_recall"}]
    assert projection["reliability"]["evidence"] == {}
    assert projection["source_overlap_detected"] is False
    assert projection["source_overlap_pairs"] == []
    assert projection["contradiction_detected"] is False
    assert projection["contradiction_count"] == 0
    assert projection["contradiction_pair_count"] == 0
    assert projection["contradiction_basis_types"] == []
    assert projection["contradiction_adjudication_enabled"] is False
    assert projection["contradiction_adjudication_applied_to_any_fact"] is False
    assert projection["contradiction_adjudication_status"] == "disabled"
    assert projection["contradiction_adjudication_candidate_count"] == 0
    assert projection["contradiction_adjudication_sent_count"] == 0
    assert projection["contradiction_adjudication_completed_count"] == 0
    assert projection["contradiction_adjudication_confirmed_count"] == 0
    assert projection["contradiction_adjudication_rejected_count"] == 0
    assert projection["contradiction_adjudication_inconclusive_count"] == 0
    assert projection["contradiction_adjudication_error_count"] == 0
    assert projection["reliability"]["score"] == "low"
    assert projection["reliability"]["cap_reason"] is None


def test_build_reliability_projection_includes_adjudication_execution_and_verdict_aggregates() -> None:
    pair = ContradictionPair(
        chunk_a_id="a",
        chunk_b_id="b",
        basis="effective_date",
        value_a="2024-03-01",
        value_b="2025-03-01",
    )
    adjudication = ContradictionAdjudicationEvidence(
        run=build_contradiction_adjudication_run(
            enabled=True,
            status="completed_with_errors",
            candidate_count=2,
            sent_count=1,
            completed_count=1,
            confirmed_count=1,
            error_count=1,
            model="gpt-4o-mini",
        ),
        items=(
            AdjudicatedContradiction(
                fact_id="fact_001",
                pair=pair,
                adjudication=ContradictionAdjudication(
                    verdict="confirmed",
                    model="gpt-4o-mini",
                ),
            ),
            AdjudicatedContradiction(
                fact_id="fact_002",
                pair=pair,
                adjudication=ContradictionAdjudication(
                    model="gpt-4o-mini",
                    skip_reason="fact_limit",
                ),
            ),
        ),
    )
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(pair,),
        contradiction_adjudication=adjudication,
    )

    payload = serialize_reliability(reliability)
    projection = build_reliability_projection(reliability)

    assert payload["evidence"]["contradiction"]["pairs"] == [
        {
            "chunk_a_id": "a",
            "chunk_b_id": "b",
            "basis": "effective_date",
            "value_a": "2024-03-01",
            "value_b": "2025-03-01",
        }
    ]
    assert payload["evidence"]["contradiction_adjudication"]["items"] == [
        {
            "fact_id": "fact_001",
            "pair": {
                "chunk_a_id": "a",
                "chunk_b_id": "b",
                "basis": "effective_date",
                "value_a": "2024-03-01",
                "value_b": "2025-03-01",
            },
            "adjudication": {
                "verdict": "confirmed",
                "rationale": None,
                "model": "gpt-4o-mini",
                "skip_reason": None,
                "error": None,
            },
        },
        {
            "fact_id": "fact_002",
            "pair": {
                "chunk_a_id": "a",
                "chunk_b_id": "b",
                "basis": "effective_date",
                "value_a": "2024-03-01",
                "value_b": "2025-03-01",
            },
            "adjudication": {
                "verdict": None,
                "rationale": None,
                "model": "gpt-4o-mini",
                "skip_reason": "fact_limit",
                "error": None,
            },
        },
    ]
    assert projection["contradiction_adjudication_enabled"] is True
    assert projection["contradiction_adjudication_applied_to_any_fact"] is True
    assert projection["contradiction_adjudication_status"] == "completed_with_errors"
    assert projection["contradiction_adjudication_candidate_count"] == 2
    assert projection["contradiction_adjudication_sent_count"] == 1
    assert projection["contradiction_adjudication_completed_count"] == 1
    assert projection["contradiction_adjudication_confirmed_count"] == 1
    assert projection["contradiction_adjudication_rejected_count"] == 0
    assert projection["contradiction_adjudication_inconclusive_count"] == 0
    assert projection["contradiction_adjudication_error_count"] == 1


def test_search_result_bundle_default_reliability_matches_canonical_empty_state() -> None:
    from backend.search.service import SearchResultBundle

    bundle = SearchResultBundle(results=[])

    assert serialize_reliability(bundle.reliability) == serialize_reliability(
        build_reliability_assessment(top_score=None, result_count=0)
    )


def test_contradiction_adjudication_evidence_skips_when_global_or_client_setting_disables_layer(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _build_contradiction_adjudication_evidence
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="adj-disabled@example.com")
    tenant = _create_client(db_session, user, name="Adj Disabled")
    tenant.settings = {
        "retrieval": {
            "contradiction_adjudication": {
                "enabled": True,
            }
        }
    }
    db_session.commit()

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="Reset password effective date March 2024.",
        metadata_json={"chunk_index": 0, "effective_date": "2024-03-01"},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="Reset password effective date March 2025.",
        metadata_json={"chunk_index": 1, "effective_date": "2025-03-01"},
    )
    pair = ContradictionPair(
        chunk_a_id=str(first.id),
        chunk_b_id=str(second.id),
        basis="effective_date",
        value_a="2024-03-01",
        value_b="2025-03-01",
    )

    monkeypatch.setattr(
        "backend.search.service.settings.contradiction_adjudication_enabled",
        False,
    )

    _canonical, obs = _build_contradiction_adjudication_evidence(
        contradiction_pairs=(pair,),
        final_results=[(first, 0.9), (second, 0.88)],
        tenant=tenant,
        api_key="sk-test",
    )

    assert _canonical is None
    assert obs.status == "skipped_global_config"
    assert obs.enabled is False
    assert obs.candidate_count == 1

    # Tenant gate is default-on: only an explicit `enabled: false` opts the
    # tenant out. Missing subkey / malformed shape keeps adjudication enabled.
    monkeypatch.setattr(
        "backend.search.service.settings.contradiction_adjudication_enabled",
        True,
    )
    tenant.settings = {
        "retrieval": {
            "contradiction_adjudication": {
                "enabled": False,
            }
        }
    }
    db_session.commit()

    _canonical, obs = _build_contradiction_adjudication_evidence(
        contradiction_pairs=(pair,),
        final_results=[(first, 0.9), (second, 0.88)],
        tenant=tenant,
        api_key="sk-test",
    )

    assert _canonical is None
    assert obs.status == "skipped_client_setting"
    assert obs.enabled is False


def test_tenant_contradiction_adjudication_enabled_defaults_to_true_for_missing_settings() -> None:
    """A tenant without the contradiction_adjudication subkey is gated ON by default.

    The tenant flag is a temporary stop-gap; pending its full removal, the
    default-on behavior keeps adjudication usable for every tenant without
    requiring manual JSON edits to ``tenant.settings``.
    """
    from backend.search.service import _tenant_contradiction_adjudication_enabled

    class _Stub:
        def __init__(self, settings: object) -> None:
            self.settings = settings

    assert _tenant_contradiction_adjudication_enabled(_Stub(None)) is True
    assert _tenant_contradiction_adjudication_enabled(_Stub({})) is True
    assert _tenant_contradiction_adjudication_enabled(_Stub({"retrieval": {}})) is True
    assert (
        _tenant_contradiction_adjudication_enabled(
            _Stub({"retrieval": {"contradiction_adjudication": {}}})
        )
        is True
    )
    assert (
        _tenant_contradiction_adjudication_enabled(
            _Stub(
                {"retrieval": {"contradiction_adjudication": {"enabled": True}}}
            )
        )
        is True
    )
    # Only an explicit false opts the tenant out.
    assert (
        _tenant_contradiction_adjudication_enabled(
            _Stub(
                {"retrieval": {"contradiction_adjudication": {"enabled": False}}}
            )
        )
        is False
    )
    # `None` tenant (no row loaded) also defaults to True so retrieval that is
    # not tenant-scoped does not silently disable the layer.
    assert _tenant_contradiction_adjudication_enabled(None) is True


def test_contradiction_adjudication_evidence_uses_stable_fact_ids_and_marks_fact_limit_skip(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _build_contradiction_adjudication_evidence
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="adj-enabled@example.com")
    tenant = _create_client(db_session, user, name="Adj Enabled")
    tenant.settings = {
        "retrieval": {
            "contradiction_adjudication": {
                "enabled": True,
            }
        }
    }
    db_session.commit()

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="Reset password effective date March 2024 and version v2.",
        metadata_json={"chunk_index": 0, "effective_date": "2024-03-01", "version": "v2"},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="Reset password effective date March 2025 and version v3.",
        metadata_json={"chunk_index": 1, "effective_date": "2025-03-01", "version": "v3"},
    )
    pairs = (
        ContradictionPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            basis="effective_date",
            value_a="2024-03-01",
            value_b="2025-03-01",
        ),
        ContradictionPair(
            chunk_a_id=str(first.id),
            chunk_b_id=str(second.id),
            basis="version",
            value_a="v2",
            value_b="v3",
        ),
    )

    monkeypatch.setattr(
        "backend.search.service.settings.contradiction_adjudication_enabled",
        True,
    )
    monkeypatch.setattr(
        "backend.search.service.settings.contradiction_adjudication_max_facts",
        1,
    )

    def fake_adjudicate_contradictions(candidates, **kwargs):
        assert [candidate.fact_id for candidate in candidates] == ["fact_001", "fact_002"]
        return build_contradiction_adjudication_run(
            enabled=True,
            status="completed",
            candidate_count=2,
            sent_count=1,
            completed_count=1,
            confirmed_count=1,
            model="gpt-4o-mini",
            items=[
                FactAdjudicationResult(
                    fact_id="fact_001",
                    adjudication=ContradictionAdjudication(
                        verdict="confirmed",
                        model="gpt-4o-mini",
                    ),
                )
            ],
        )

    monkeypatch.setattr(
        "backend.search.service.adjudicate_contradictions",
        fake_adjudicate_contradictions,
    )

    canonical, _obs = _build_contradiction_adjudication_evidence(
        contradiction_pairs=pairs,
        final_results=[(first, 0.9), (second, 0.88)],
        tenant=tenant,
        api_key="sk-test",
    )

    assert canonical is not None
    assert canonical.run.sent_count == 1
    assert [item.fact_id for item in canonical.items] == ["fact_001", "fact_002"]
    assert canonical.items[0].adjudication is not None
    assert canonical.items[0].adjudication.verdict == "confirmed"
    assert canonical.items[1].adjudication is not None
    assert canonical.items[1].adjudication.skip_reason == "fact_limit"


def test_contradiction_adjudication_fail_open_keeps_deterministic_reliability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = ContradictionPair(
        chunk_a_id="a",
        chunk_b_id="b",
        basis="effective_date",
        value_a="2024-03-01",
        value_b="2025-03-01",
    )
    adjudication = ContradictionAdjudicationEvidence(
        run=build_contradiction_adjudication_run(
            enabled=True,
            status="failed_open",
            candidate_count=1,
            sent_count=1,
            error_count=1,
            model="gpt-4o-mini",
        ),
        items=(
            AdjudicatedContradiction(
                fact_id="fact_001",
                pair=pair,
                adjudication=ContradictionAdjudication(
                    model="gpt-4o-mini",
                    error="timeout",
                ),
            ),
        ),
    )
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_pairs=(pair,),
        contradiction_adjudication=adjudication,
        contradiction_adjudication_observability=adjudication.run,
    )

    assert reliability.score == "high"
    assert reliability.cap_reason is None
    projection = build_reliability_projection(reliability)
    assert projection["contradiction_detected"] is True
    assert projection["contradiction_adjudication_status"] == "failed_open"
    assert projection["contradiction_adjudication_error_count"] == 1


@pytest.mark.rag_edge
def test_adjudicate_contradictions_records_partial_malformed_item_as_error(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(
            message=Mock(
                content=(
                    '{"items":['
                    '{"fact_id":"fact_001","verdict":"confirmed","rationale":"Metadata disagrees clearly."},'
                    '{"fact_id":"fact_002","verdict":"maybe"}'
                    "]}"
                )
            )
        )
    ]

    run = adjudicate_contradictions(
        [
            ContradictionAdjudicationCandidate(
                fact_id="fact_001",
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
                preview_a="Chunk A",
                preview_b="Chunk B",
            ),
            ContradictionAdjudicationCandidate(
                fact_id="fact_002",
                chunk_a_id="a",
                chunk_b_id="b",
                basis="version",
                value_a="v2",
                value_b="v3",
                preview_a="Chunk A",
                preview_b="Chunk B",
            ),
        ],
        api_key="sk-test",
        model="gpt-4o-mini",
        max_facts=5,
        preview_chars=120,
        max_completion_tokens=300,
    )

    assert run.status == "completed_with_errors"
    assert run.candidate_count == 2
    assert run.sent_count == 2
    assert run.completed_count == 1
    assert run.confirmed_count == 1
    assert run.error_count == 1
    assert run.items[0].fact_id == "fact_001"
    assert run.items[0].adjudication.verdict == "confirmed"
    assert run.items[1].fact_id == "fact_002"
    assert run.items[1].adjudication.error == "invalid_verdict"


def test_adjudicate_contradictions_skips_empty_batch_without_openai_call(
    mock_openai_client: Mock,
) -> None:
    run = adjudicate_contradictions(
        [
            ContradictionAdjudicationCandidate(
                fact_id="fact_001",
                chunk_a_id="a",
                chunk_b_id="b",
                basis="effective_date",
                value_a="2024-03-01",
                value_b="2025-03-01",
                preview_a="Chunk A",
                preview_b="Chunk B",
            ),
        ],
        api_key="sk-test",
        model="gpt-4o-mini",
        max_facts=0,
        preview_chars=120,
        max_completion_tokens=300,
    )
    assert run.status == "skipped_fact_limit"
    assert run.sent_count == 0
    mock_openai_client.chat.completions.create.assert_not_called()


def test_serialize_reliability_omits_contradiction_adjudication_for_observability_only() -> None:
    reliability = build_reliability_assessment(
        top_score=0.9,
        result_count=5,
        contradiction_adjudication=None,
        contradiction_adjudication_observability=build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_no_candidates",
            candidate_count=0,
            model="gpt-4o-mini",
        ),
    )
    payload = serialize_reliability(reliability)
    assert "contradiction_adjudication" not in payload["evidence"]
