"""Retrieval reliability + contradiction detection/adjudication contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from backend.core.config import settings
from backend.models import Embedding
from backend.search.contradiction_adjudication import (
    ContradictionAdjudication,
    ContradictionAdjudicationCandidate,
    ContradictionAdjudicationRun,
    adjudicate_contradictions,
    build_contradiction_adjudication_run,
    serialize_contradiction_adjudication,
    serialize_contradiction_adjudication_run,
)

MAX_OVERLAP_CHECK_CANDIDATES = 5

ReliabilityScore = Literal["low", "medium", "high"]
ReliabilityCapReason = Literal["source_overlap", "contradiction"]
ReliabilitySignalKind = Literal[
    "source_overlap",
    "low_top_score",
    "weak_recall",
    "contradiction",
]

RELIABILITY_SIGNAL_ORDER: tuple[ReliabilitySignalKind, ...] = (
    "source_overlap",
    "low_top_score",
    "weak_recall",
    "contradiction",
)
HIGH_RELIABILITY_SCORE_THRESHOLD = 0.8
LOW_RELIABILITY_SCORE_THRESHOLD = 0.45
WEAK_RECALL_RESULT_COUNT_THRESHOLD = 2
CONTRADICTION_DATE_KEYS: tuple[str, ...] = ("effective_date",)
CONTRADICTION_VERSION_KEYS: tuple[str, ...] = ("version", "revision")
CONTRADICTION_ADJUDICATION_FACT_LIMIT_SKIP_REASON = "fact_limit"


@dataclass(frozen=True)
class ReliabilitySignal:
    """Compact canonical reliability signal entry."""

    kind: ReliabilitySignalKind


@dataclass(frozen=True)
class SourceOverlapPair:
    """Structured overlap evidence between two different documents."""

    chunk_a_id: str
    chunk_b_id: str
    similarity: float
    signal_type: Literal["cross_document_overlap"] = "cross_document_overlap"


@dataclass(frozen=True)
class SourceOverlapEvidence:
    """Debug/trace-only overlap evidence kept separate from compact signals."""

    pairs: tuple[SourceOverlapPair, ...] = ()
    similarity_threshold: float | None = None


@dataclass(frozen=True)
class ReliabilityEvidence:
    """Structured evidence families for canonical reliability payloads."""

    source_overlap: SourceOverlapEvidence | None = None
    contradiction: ContradictionEvidence | None = None
    contradiction_adjudication: ContradictionAdjudicationEvidence | None = None


@dataclass(frozen=True)
class ContradictionPair:
    """Canonical contradiction fact entry for one overlap-admitted logical pair."""

    chunk_a_id: str
    chunk_b_id: str
    basis: str
    value_a: str
    value_b: str


@dataclass(frozen=True)
class ContradictionEvidence:
    """Canonical contradiction evidence; `pairs` is a flat fact-level entry list."""

    pairs: tuple[ContradictionPair, ...] = ()


@dataclass(frozen=True)
class AdjudicatedContradiction:
    """One deterministic contradiction fact plus its optional adjudication payload."""

    fact_id: str
    pair: ContradictionPair
    adjudication: ContradictionAdjudication | None = None


@dataclass(frozen=True)
class ContradictionAdjudicationEvidence:
    """Run-level adjudication summary plus per-fact linked results."""

    run: ContradictionAdjudicationRun
    items: tuple[AdjudicatedContradiction, ...] = ()


@dataclass(frozen=True)
class ContradictionPolicyEvaluation:
    """Effective contradiction facts plus the cap decision derived from them."""

    effective_pairs: tuple[ContradictionPair, ...] = ()
    threshold_reached: bool = False


@dataclass(frozen=True)
class RetrievalReliability:
    """Canonical structured retrieval reliability contract."""

    base_score: ReliabilityScore = "low"
    score: ReliabilityScore = "low"
    cap: ReliabilityScore | None = None
    cap_reason: ReliabilityCapReason | None = None
    signals: tuple[ReliabilitySignal, ...] = ()
    evidence: ReliabilityEvidence = field(default_factory=ReliabilityEvidence)
    """Shadow-layer adjudication run for traces/debug only; never serialized in `serialize_reliability`."""
    contradiction_adjudication_observability: ContradictionAdjudicationRun | None = None

    @property
    def source_overlap_detected(self) -> bool:
        return any(signal.kind == "source_overlap" for signal in self.signals)

    @property
    def source_overlap_pairs(self) -> list[dict[str, object]]:
        overlap_evidence = self.evidence.source_overlap
        if overlap_evidence is None:
            return []
        return [serialize_source_overlap_pair(pair) for pair in overlap_evidence.pairs]


def serialize_source_overlap_pair(pair: SourceOverlapPair) -> dict[str, object]:
    """Serialize one canonical overlap pair without mutating the source object."""
    return {
        "chunk_a_id": pair.chunk_a_id,
        "chunk_b_id": pair.chunk_b_id,
        "similarity": pair.similarity,
        "signal_type": pair.signal_type,
    }


def serialize_contradiction_pair(pair: ContradictionPair) -> dict[str, object]:
    """Serialize one canonical contradiction pair without mutating the source object."""
    return {
        "chunk_a_id": pair.chunk_a_id,
        "chunk_b_id": pair.chunk_b_id,
        "basis": pair.basis,
        "value_a": pair.value_a,
        "value_b": pair.value_b,
    }


def serialize_adjudicated_contradiction(
    item: AdjudicatedContradiction,
) -> dict[str, object]:
    """Serialize one adjudicated contradiction without mutating the source object."""
    return {
        "fact_id": item.fact_id,
        "pair": serialize_contradiction_pair(item.pair),
        "adjudication": (
            serialize_contradiction_adjudication(item.adjudication)
            if item.adjudication is not None
            else None
        ),
    }


def serialize_reliability(reliability: RetrievalReliability) -> dict[str, object]:
    """Serialize the canonical reliability object with stable empty containers."""
    evidence: dict[str, object] = {}
    overlap_evidence = reliability.evidence.source_overlap
    if overlap_evidence is not None:
        evidence["source_overlap"] = {
            "pairs": [serialize_source_overlap_pair(pair) for pair in overlap_evidence.pairs],
            "similarity_threshold": overlap_evidence.similarity_threshold,
        }
    contradiction_evidence = reliability.evidence.contradiction
    if contradiction_evidence is not None:
        evidence["contradiction"] = {
            "pairs": [
                serialize_contradiction_pair(pair)
                for pair in contradiction_evidence.pairs
            ]
        }
    contradiction_adjudication = reliability.evidence.contradiction_adjudication
    if contradiction_adjudication is not None:
        adjudication_payload = serialize_contradiction_adjudication_run(
            contradiction_adjudication.run
        )
        adjudication_payload["items"] = [
            serialize_adjudicated_contradiction(item)
            for item in contradiction_adjudication.items
        ]
        evidence["contradiction_adjudication"] = adjudication_payload
    return {
        "base_score": reliability.base_score,
        "score": reliability.score,
        "cap": reliability.cap,
        "cap_reason": reliability.cap_reason,
        "signals": [{"kind": signal.kind} for signal in reliability.signals],
        "evidence": evidence,
    }


def build_reliability_projection(
    reliability: RetrievalReliability,
) -> dict[str, object]:
    """Project canonical reliability into trace/debug-friendly payloads."""
    reliability_payload = serialize_reliability(reliability)
    return {
        "reliability": reliability_payload,
        "source_overlap_detected": reliability.source_overlap_detected,
        "source_overlap_pairs": reliability.source_overlap_pairs,
        **_build_contradiction_projection_fields(reliability_payload),
        **_build_contradiction_adjudication_projection_fields(
            reliability_payload,
            reliability.contradiction_adjudication_observability,
        ),
    }


def _build_reliability_signals(
    kinds: list[ReliabilitySignalKind],
) -> tuple[ReliabilitySignal, ...]:
    """Deduplicate signal kinds and serialize them in stable order."""
    ordered_kinds = {
        kind
        for kind in RELIABILITY_SIGNAL_ORDER
        if kind in set(kinds)
    }
    return tuple(
        ReliabilitySignal(kind=kind)
        for kind in RELIABILITY_SIGNAL_ORDER
        if kind in ordered_kinds
    )


def _compute_base_reliability_score(
    *,
    top_score: float | None,
    result_count: int,
) -> ReliabilityScore:
    """Compute the raw categorical score before applying caps."""
    if result_count == 0 or top_score is None:
        return "low"
    if top_score >= HIGH_RELIABILITY_SCORE_THRESHOLD:
        return "high"
    if top_score >= LOW_RELIABILITY_SCORE_THRESHOLD:
        return "medium"
    return "low"


def _contradiction_identity(
    pair: ContradictionPair,
) -> tuple[str, str, str, str, str]:
    """Return the canonical duplicate identity for one contradiction fact."""
    if (pair.chunk_a_id, pair.chunk_b_id) <= (pair.chunk_b_id, pair.chunk_a_id):
        return (
            pair.chunk_a_id,
            pair.chunk_b_id,
            pair.basis,
            pair.value_a,
            pair.value_b,
        )
    return (
        pair.chunk_b_id,
        pair.chunk_a_id,
        pair.basis,
        pair.value_b,
        pair.value_a,
    )


def _logical_overlap_pair_identity_from_ids(
    chunk_a_id: str,
    chunk_b_id: str,
) -> tuple[str, str]:
    """Return the orientation-insensitive identity for one logical overlap pair."""
    return tuple(sorted((chunk_a_id, chunk_b_id)))


def _logical_overlap_pair_identity(pair: ContradictionPair) -> tuple[str, str]:
    """Return the orientation-insensitive identity for one logical overlap pair."""
    return _logical_overlap_pair_identity_from_ids(pair.chunk_a_id, pair.chunk_b_id)


def _build_contradiction_projection_fields(
    reliability_payload: dict[str, object],
) -> dict[str, object]:
    """Derive observability-only contradiction metrics from final canonical payload."""
    contradiction_pairs_payload: list[dict[str, object]] = []
    evidence_payload = reliability_payload.get("evidence")
    if isinstance(evidence_payload, dict):
        contradiction_payload = evidence_payload.get("contradiction")
        if isinstance(contradiction_payload, dict):
            pairs_payload = contradiction_payload.get("pairs")
            if isinstance(pairs_payload, list):
                contradiction_pairs_payload = [
                    pair_payload
                    for pair_payload in pairs_payload
                    if isinstance(pair_payload, dict)
                ]

    contradiction_count = len(contradiction_pairs_payload)
    if contradiction_count == 0:
        return {
            "contradiction_detected": False,
            "contradiction_count": 0,
            "contradiction_pair_count": 0,
            "contradiction_basis_types": [],
        }

    contradiction_basis_types: list[str] = []
    seen_basis_types: set[str] = set()
    logical_pair_identities: set[tuple[str, str]] = set()

    for pair_payload in contradiction_pairs_payload:
        basis = pair_payload.get("basis")
        if isinstance(basis, str) and basis not in seen_basis_types:
            seen_basis_types.add(basis)
            contradiction_basis_types.append(basis)

        chunk_a_id = pair_payload.get("chunk_a_id")
        chunk_b_id = pair_payload.get("chunk_b_id")
        if isinstance(chunk_a_id, str) and isinstance(chunk_b_id, str):
            logical_pair_identities.add(
                _logical_overlap_pair_identity_from_ids(chunk_a_id, chunk_b_id)
            )

    return {
        "contradiction_detected": True,
        "contradiction_count": contradiction_count,
        "contradiction_pair_count": len(logical_pair_identities),
        "contradiction_basis_types": contradiction_basis_types,
    }


def _build_contradiction_adjudication_projection_fields(
    reliability_payload: dict[str, object],
    observability: ContradictionAdjudicationRun | None,
) -> dict[str, object]:
    """Derive observability-only adjudication metrics (prefer shadow run over canonical evidence)."""
    defaults = {
        "contradiction_adjudication_applied_to_any_fact": False,
        "contradiction_adjudication_status": "disabled",
        "contradiction_adjudication_candidate_count": 0,
        "contradiction_adjudication_sent_count": 0,
        "contradiction_adjudication_completed_count": 0,
        "contradiction_adjudication_confirmed_count": 0,
        "contradiction_adjudication_rejected_count": 0,
        "contradiction_adjudication_inconclusive_count": 0,
        "contradiction_adjudication_error_count": 0,
    }

    if observability is not None:
        return {
            "contradiction_adjudication_applied_to_any_fact": observability.applied_to_any_fact,
            "contradiction_adjudication_status": observability.status,
            "contradiction_adjudication_candidate_count": observability.candidate_count,
            "contradiction_adjudication_sent_count": observability.sent_count,
            "contradiction_adjudication_completed_count": observability.completed_count,
            "contradiction_adjudication_confirmed_count": observability.confirmed_count,
            "contradiction_adjudication_rejected_count": observability.rejected_count,
            "contradiction_adjudication_inconclusive_count": observability.inconclusive_count,
            "contradiction_adjudication_error_count": observability.error_count,
        }

    evidence_payload = reliability_payload.get("evidence")
    if not isinstance(evidence_payload, dict):
        return defaults

    adjudication_payload = evidence_payload.get("contradiction_adjudication")
    if not isinstance(adjudication_payload, dict):
        return defaults

    return {
        "contradiction_adjudication_applied_to_any_fact": bool(
            adjudication_payload.get("applied_to_any_fact", False)
        ),
        "contradiction_adjudication_status": str(
            adjudication_payload.get("status", "disabled")
        ),
        "contradiction_adjudication_candidate_count": int(
            adjudication_payload.get("candidate_count", 0)
        ),
        "contradiction_adjudication_sent_count": int(
            adjudication_payload.get("sent_count", 0)
        ),
        "contradiction_adjudication_completed_count": int(
            adjudication_payload.get("completed_count", 0)
        ),
        "contradiction_adjudication_confirmed_count": int(
            adjudication_payload.get("confirmed_count", 0)
        ),
        "contradiction_adjudication_rejected_count": int(
            adjudication_payload.get("rejected_count", 0)
        ),
        "contradiction_adjudication_inconclusive_count": int(
            adjudication_payload.get("inconclusive_count", 0)
        ),
        "contradiction_adjudication_error_count": int(
            adjudication_payload.get("error_count", 0)
        ),
    }


def _is_valid_contradiction_pair(pair: ContradictionPair) -> bool:
    """Keep only contradiction facts with the full canonical payload present."""
    return all(
        isinstance(value, str) and value.strip()
        for value in (
            pair.chunk_a_id,
            pair.chunk_b_id,
            pair.basis,
            pair.value_a,
            pair.value_b,
        )
    )


def _evaluate_contradiction_policy(
    contradiction_pairs: tuple[ContradictionPair, ...],
) -> ContradictionPolicyEvaluation:
    """
    Evaluate contradiction severity from effective contradiction facts.

    V1 removes only invalid facts and exact duplicate emissions; it does not
    merge semantically distinct contradictions for scoring purposes.
    """
    effective_pairs: list[ContradictionPair] = []
    seen_identities: set[tuple[str, str, str, str, str]] = set()
    facts_per_overlap_pair: dict[tuple[str, str], int] = {}

    for pair in contradiction_pairs:
        if not _is_valid_contradiction_pair(pair):
            continue
        identity = _contradiction_identity(pair)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        effective_pairs.append(pair)
        overlap_pair_identity = _logical_overlap_pair_identity(pair)
        facts_per_overlap_pair[overlap_pair_identity] = (
            facts_per_overlap_pair.get(overlap_pair_identity, 0) + 1
        )

    threshold_reached = any(
        fact_count >= 2
        for fact_count in facts_per_overlap_pair.values()
    ) or len(facts_per_overlap_pair) >= 2
    return ContradictionPolicyEvaluation(
        effective_pairs=tuple(effective_pairs),
        threshold_reached=threshold_reached,
    )


def _normalize_date_value(raw_value: object) -> tuple[int, int | None, int | None] | None:
    """Normalize YYYY / YYYY-MM / YYYY-MM-DD style dates for conservative comparison."""
    if not isinstance(raw_value, str):
        return None
    value = raw_value.strip()
    if not value:
        return None
    match = re.fullmatch(r"(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?", value)
    if match is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) is not None else None
    day = int(match.group(3)) if match.group(3) is not None else None
    return (year, month, day)


def _dates_contradict(
    first_value: tuple[int, int | None, int | None],
    second_value: tuple[int, int | None, int | None],
) -> bool:
    """Treat different granularity as compatible when shared known components match."""
    first_year, first_month, first_day = first_value
    second_year, second_month, second_day = second_value
    if first_year != second_year:
        return True
    if first_month is not None and second_month is not None and first_month != second_month:
        return True
    if first_day is not None and second_day is not None and first_day != second_day:
        return True
    return False


def _normalize_version_value(raw_value: object) -> tuple[int, ...] | None:
    """Normalize versions like `v2`, `2.0`, and `2.1.0` for conservative comparison."""
    if not isinstance(raw_value, str):
        return None
    value = raw_value.strip().casefold()
    if not value:
        return None
    value = re.sub(r"^(?:version|revision|rev)\s*", "", value)
    value = value.lstrip("v")
    if not re.fullmatch(r"\d+(?:\.\d+)*", value):
        return None
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _metadata_contradiction_pairs(
    first: Embedding,
    second: Embedding,
) -> tuple[ContradictionPair, ...]:
    """
    Detect narrow contradiction indicators from explicit metadata on one overlap pair.

    One overlap-admitted chunk pair may legitimately yield multiple contradiction
    facts, for example separate disagreements on `effective_date` and `version`.
    This helper intentionally returns fact-level evidence rather than forcing a
    one-entry-per-pair summary.
    """
    first_meta = first.metadata_json or {}
    second_meta = second.metadata_json or {}
    contradiction_pairs: list[ContradictionPair] = []
    for key in CONTRADICTION_DATE_KEYS:
        first_raw = first_meta.get(key)
        second_raw = second_meta.get(key)
        if first_raw is None or second_raw is None:
            continue
        if not isinstance(first_raw, str) or not isinstance(second_raw, str):
            continue
        first_normalized = _normalize_date_value(first_raw)
        second_normalized = _normalize_date_value(second_raw)
        if first_normalized is None or second_normalized is None:
            continue
        if not _dates_contradict(first_normalized, second_normalized):
            continue
        contradiction_pairs.append(
            ContradictionPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                basis=key,
                value_a=first_raw,
                value_b=second_raw,
            )
        )
    for key in CONTRADICTION_VERSION_KEYS:
        first_raw = first_meta.get(key)
        second_raw = second_meta.get(key)
        if first_raw is None or second_raw is None:
            continue
        if not isinstance(first_raw, str) or not isinstance(second_raw, str):
            continue
        first_normalized = _normalize_version_value(first_raw)
        second_normalized = _normalize_version_value(second_raw)
        if first_normalized is None or second_normalized is None:
            continue
        if first_normalized == second_normalized:
            continue
        contradiction_pairs.append(
            ContradictionPair(
                chunk_a_id=str(first.id),
                chunk_b_id=str(second.id),
                basis=key,
                value_a=first_raw,
                value_b=second_raw,
            )
        )
    return tuple(contradiction_pairs)


def detect_metadata_contradictions(
    candidates: list[tuple[Embedding, float]],
    overlap_pairs: tuple[SourceOverlapPair, ...],
) -> tuple[ContradictionPair, ...]:
    """Inspect only overlap-admitted pairs for narrow metadata contradiction indicators."""
    if not overlap_pairs:
        return ()
    candidates_by_id = {
        str(embedding.id): embedding
        for embedding, _ in candidates[:MAX_OVERLAP_CHECK_CANDIDATES]
    }
    contradiction_pairs: list[ContradictionPair] = []
    for overlap_pair in overlap_pairs:
        first = candidates_by_id.get(overlap_pair.chunk_a_id)
        second = candidates_by_id.get(overlap_pair.chunk_b_id)
        if first is None or second is None:
            continue
        contradiction_pairs.extend(_metadata_contradiction_pairs(first, second))
    return tuple(contradiction_pairs)


def _candidate_preview_text(embedding: Embedding) -> str:
    """Return one stable preview source for contradiction adjudication."""
    return embedding.chunk_text or ""


def _candidate_adjudication_metadata(
    embedding: Embedding,
    *,
    basis: str,
) -> dict[str, object]:
    """Return only compact metadata relevant for contradiction adjudication."""
    metadata = embedding.metadata_json if isinstance(embedding.metadata_json, dict) else {}
    relevant: dict[str, object] = {
        "chunk_index": metadata.get("chunk_index"),
        "basis_value": metadata.get(basis),
    }
    filename = metadata.get("filename")
    if isinstance(filename, str) and filename.strip():
        relevant["filename"] = filename
    return relevant


def _build_contradiction_adjudication_evidence(
    *,
    contradiction_pairs: tuple[ContradictionPair, ...],
    final_results: list[tuple[Embedding, float]],
    api_key: str | None,
) -> tuple[ContradictionAdjudicationEvidence | None, ContradictionAdjudicationRun]:
    """
    Build shadow adjudication observability plus optional canonical adjudication evidence.

    Skip-only states never produce canonical `evidence.contradiction_adjudication`;
    they only populate the returned observability run for traces/debug.
    Canonical adjudication evidence is present only after a non-empty LLM batch
    (`sent_count > 0`) or a failed-open path that attempted a batch.
    """
    model = settings.contradiction_adjudication_model
    effective_pairs = _evaluate_contradiction_policy(contradiction_pairs).effective_pairs
    candidate_count = len(effective_pairs)

    if candidate_count == 0:
        return None, build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_no_candidates",
            candidate_count=0,
            model=model,
        )

    if not api_key:
        return None, build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_missing_client_key",
            candidate_count=candidate_count,
            model=model,
        )

    candidates_by_id = {str(embedding.id): embedding for embedding, _ in final_results}
    adjudication_candidates: list[ContradictionAdjudicationCandidate] = []
    ordered_pairs: list[tuple[str, ContradictionPair]] = []
    for index, pair in enumerate(effective_pairs, start=1):
        first = candidates_by_id.get(pair.chunk_a_id)
        second = candidates_by_id.get(pair.chunk_b_id)
        if first is None or second is None:
            continue
        fact_id = f"fact_{index:03d}"
        ordered_pairs.append((fact_id, pair))
        adjudication_candidates.append(
            ContradictionAdjudicationCandidate(
                fact_id=fact_id,
                chunk_a_id=pair.chunk_a_id,
                chunk_b_id=pair.chunk_b_id,
                basis=pair.basis,
                value_a=pair.value_a,
                value_b=pair.value_b,
                preview_a=_candidate_preview_text(first),
                preview_b=_candidate_preview_text(second),
                metadata_a=_candidate_adjudication_metadata(first, basis=pair.basis),
                metadata_b=_candidate_adjudication_metadata(second, basis=pair.basis),
            )
        )

    if not adjudication_candidates:
        return None, build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_no_candidates",
            candidate_count=candidate_count,
            model=model,
        )

    max_facts = settings.contradiction_adjudication_max_facts
    if max_facts <= 0:
        return None, build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_fact_limit",
            candidate_count=candidate_count,
            sent_count=0,
            model=model,
            applied_to_any_fact=False,
        )

    run = adjudicate_contradictions(
        adjudication_candidates,
        api_key=api_key,
        model=model,
        max_facts=max_facts,
        preview_chars=settings.contradiction_adjudication_preview_chars,
        max_completion_tokens=settings.contradiction_adjudication_max_tokens,
    )

    if run.sent_count == 0:
        return None, run

    adjudication_by_fact_id = {
        item.fact_id: item.adjudication
        for item in run.items
    }
    items: list[AdjudicatedContradiction] = []
    for position, (fact_id, pair) in enumerate(ordered_pairs, start=1):
        adjudication = adjudication_by_fact_id.get(fact_id)
        if adjudication is None and position > run.sent_count:
            adjudication = ContradictionAdjudication(
                skip_reason=CONTRADICTION_ADJUDICATION_FACT_LIMIT_SKIP_REASON,
                model=run.model,
            )
        items.append(
            AdjudicatedContradiction(
                fact_id=fact_id,
                pair=pair,
                adjudication=adjudication,
            )
        )

    return (
        ContradictionAdjudicationEvidence(
            run=run,
            items=tuple(items),
        ),
        run,
    )


def _adjudication_suppresses_contradiction_cap(
    contradiction_adjudication: ContradictionAdjudicationEvidence | None,
) -> bool:
    """
    Decide whether LLM adjudication should drop the deterministic contradiction cap.

    v1 rule (intentionally strict, fail-open): suppress only when every adjudicated
    item returned `verdict == "rejected"`. Any other state — `confirmed`,
    `inconclusive`, `error`, `skip_reason`, mixed verdicts, partial coverage where
    some facts were not sent, or a `failed_open`/non-completed run — leaves the
    deterministic cap untouched.
    """
    if contradiction_adjudication is None:
        return False
    run = contradiction_adjudication.run
    if run.status not in {"completed", "completed_with_errors"}:
        return False
    if run.sent_count <= 0:
        return False
    items = contradiction_adjudication.items
    if not items:
        return False
    return all(
        item.adjudication is not None and item.adjudication.verdict == "rejected"
        for item in items
    )


def build_reliability_assessment(
    *,
    top_score: float | None,
    result_count: int,
    source_overlap_detected: bool = False,
    source_overlap_pairs: tuple[SourceOverlapPair, ...] = (),
    source_overlap_similarity_threshold: float | None = None,
    contradiction_pairs: tuple[ContradictionPair, ...] = (),
    contradiction_adjudication: ContradictionAdjudicationEvidence | None = None,
    contradiction_adjudication_observability: ContradictionAdjudicationRun | None = None,
) -> RetrievalReliability:
    """
    Build the canonical retrieval reliability object in one place.

    `source_overlap_detected=True` with empty `source_overlap_pairs` is allowed
    as a compatibility/mock state even though the real overlap detector normally
    emits both together. Empty retrieval output intentionally records
    `weak_recall` as a diagnostic signal rather than producing a signal-free
    object.
    """
    base_score = _compute_base_reliability_score(
        top_score=top_score,
        result_count=result_count,
    )
    contradiction_policy = _evaluate_contradiction_policy(contradiction_pairs)
    effective_contradiction_pairs = contradiction_policy.effective_pairs
    signal_kinds: list[ReliabilitySignalKind] = []
    if source_overlap_detected:
        signal_kinds.append("source_overlap")
    if effective_contradiction_pairs:
        signal_kinds.append("contradiction")
    if top_score is not None and top_score < LOW_RELIABILITY_SCORE_THRESHOLD:
        signal_kinds.append("low_top_score")
    if result_count < WEAK_RECALL_RESULT_COUNT_THRESHOLD:
        signal_kinds.append("weak_recall")

    cap: ReliabilityScore | None = None
    cap_reason: ReliabilityCapReason | None = None
    score = base_score
    contradiction_cap_suppressed_by_adjudication = (
        contradiction_policy.threshold_reached
        and _adjudication_suppresses_contradiction_cap(contradiction_adjudication)
    )
    if contradiction_policy.threshold_reached and not contradiction_cap_suppressed_by_adjudication:
        cap = "low"
        cap_reason = "contradiction"
        score = "low"
    elif source_overlap_detected and base_score == "high":
        cap = "medium"
        cap_reason = "source_overlap"
        score = "medium"

    evidence = ReliabilityEvidence()
    if source_overlap_pairs or effective_contradiction_pairs or contradiction_adjudication:
        evidence = ReliabilityEvidence(
            source_overlap=(
                SourceOverlapEvidence(
                    pairs=source_overlap_pairs,
                    similarity_threshold=source_overlap_similarity_threshold,
                )
                if source_overlap_pairs
                else None
            ),
            contradiction=(
                ContradictionEvidence(pairs=effective_contradiction_pairs)
                if effective_contradiction_pairs
                else None
            ),
            contradiction_adjudication=contradiction_adjudication,
        )

    return RetrievalReliability(
        base_score=base_score,
        score=score,
        cap=cap,
        cap_reason=cap_reason,
        signals=_build_reliability_signals(signal_kinds),
        evidence=evidence,
        contradiction_adjudication_observability=contradiction_adjudication_observability,
    )


def default_retrieval_reliability() -> RetrievalReliability:
    """Return the one canonical empty/default reliability state."""
    return build_reliability_assessment(
        top_score=None,
        result_count=0,
    )
