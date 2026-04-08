"""Business logic for vector similarity search."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.models import Client, Document, Embedding
from backend.observability import TraceHandle
from backend.observability.formatters import (
    format_embedding_results,
    format_query_embedding_preview,
    truncate_text,
)
from backend.search.contradiction_adjudication import (
    ContradictionAdjudication,
    ContradictionAdjudicationCandidate,
    ContradictionAdjudicationRun,
    adjudicate_contradictions,
    build_contradiction_adjudication_run,
    serialize_contradiction_adjudication,
    serialize_contradiction_adjudication_run,
)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# Number of vector candidates to pre-fetch before BM25 scoring.
# BM25 runs only on this pool (already in memory) — never queries all client chunks.
BM25_CANDIDATE_POOL = 200
RRF_CANDIDATE_POOL_MULTIPLIER = 4
RERANK_LEXICAL_WEIGHT = 0.35
RERANK_VECTOR_WEIGHT = 0.25
RERANK_BM25_WEIGHT = 0.20
RERANK_RRF_WEIGHT = 0.20
SCRIPT_BOOST_FACTOR = 0.1
MMR_LAMBDA = 0.7
CYRILLIC_LANGUAGE_PREFIXES = ("ru", "uk", "bg", "sr", "mk", "be")
LATIN_LANGUAGE_PREFIXES = ("en", "es", "fr", "de", "it", "pt", "tr", "nl")
MAX_OVERLAP_CHECK_CANDIDATES = 5
BM25_DEBUG_VARIANT_TEXT_MAX_LEN = 80

ReliabilityScore = Literal["low", "medium", "high"]
ReliabilityCapReason = Literal["source_overlap", "contradiction"]
ReliabilitySignalKind = Literal[
    "source_overlap",
    "low_top_score",
    "weak_recall",
    "contradiction",
]
VariantMode = Literal["single", "multi"]
BM25ExpansionMode = Literal["asymmetric", "symmetric_variants"]

logger = logging.getLogger(__name__)


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
CONTRADICTION_ADJUDICATION_SETTINGS_KEY = "contradiction_adjudication"
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
        "contradiction_adjudication_enabled": False,
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
            "contradiction_adjudication_enabled": observability.enabled,
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
        "contradiction_adjudication_enabled": bool(
            adjudication_payload.get("enabled", False)
        ),
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


def _client_contradiction_adjudication_enabled(client: Client | None) -> bool:
    """Resolve per-client contradiction adjudication override from JSON settings."""
    if client is None or not isinstance(client.settings, dict):
        return False
    retrieval_settings = client.settings.get("retrieval")
    if not isinstance(retrieval_settings, dict):
        return False
    contradiction_settings = retrieval_settings.get(
        CONTRADICTION_ADJUDICATION_SETTINGS_KEY
    )
    if not isinstance(contradiction_settings, dict):
        return False
    return contradiction_settings.get("enabled") is True


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
    client: Client | None,
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

    if not settings.contradiction_adjudication_enabled:
        return None, build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_global_config",
            candidate_count=candidate_count,
            model=model,
        )

    if not _client_contradiction_adjudication_enabled(client):
        return None, build_contradiction_adjudication_run(
            enabled=False,
            status="skipped_client_setting",
            candidate_count=candidate_count,
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
        max_tokens=settings.contradiction_adjudication_max_tokens,
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
    if contradiction_policy.threshold_reached:
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


@dataclass
class SearchResultBundle:
    """Ranked retrieval results plus raw signals used for confidence decisions."""

    results: list[tuple[Embedding, float]]
    best_vector_similarity: float | None = None
    # For each returned final chunk: cosine similarity from vector-candidate stage.
    # If a chunk came only from lexical/BM25 path (no vector candidate), value is None.
    vector_similarities: list[float | None] | None = None
    best_keyword_score: float | None = None
    has_lexical_signal: bool = False
    query_variants: list[str] | None = None
    query_script_bucket: str | None = None
    reliability: RetrievalReliability = field(default_factory=default_retrieval_reliability)
    query_variant_count: int = 1
    variant_mode: VariantMode = "single"
    extra_variant_count: int = 0
    embedded_query_count: int = 1
    extra_embedded_queries: int = 0
    embedding_api_request_count: int = 1
    extra_embedding_api_requests: int = 0
    vector_search_call_count: int = 0
    extra_vector_search_calls: int = 0
    bm25_expansion_mode: BM25ExpansionMode = "asymmetric"
    bm25_query_variant_count: int = 1
    bm25_variant_eval_count: int = 1
    extra_bm25_variant_evals: int = 0
    bm25_merged_hit_count_before_cap: int = 0
    bm25_merged_hit_count_after_cap: int = 0
    retrieval_duration_ms: float = 0.0
    query_embedding_duration_ms: float = 0.0
    vector_search_duration_ms: float = 0.0


@dataclass
class MMRSelectionResult:
    """MMR selection order plus separate debug metadata for observability."""

    results: list[tuple[Embedding, float]]
    replacements: list[dict[str, object]]
    diagnostics: list[dict[str, object]]


@dataclass
class VectorCandidateSet:
    """Shared vector candidate-set construction output before lexical stages."""

    candidates: list[tuple[Embedding, float]]
    call_count: int
    duration_ms: float


@dataclass
class PreparedBM25Corpus:
    """Reusable BM25 scorer over the shared in-memory candidate corpus."""

    candidates: list[Embedding]
    scorer: BM25Okapi | None


@dataclass
class BM25Winner:
    """Winning lexical-safe variant provenance for one merged BM25 hit."""

    variant_index: int
    variant_query: str
    score: float


@dataclass
class BM25SearchBundle:
    """Merged BM25 branch output plus explicit expansion/debug metadata."""

    results: list[tuple[Embedding, float]]
    has_lexical_signal: bool
    variant_queries: list[str]
    variant_eval_count: int
    merged_hit_count_before_cap: int
    merged_hit_count_after_cap: int
    winner_by_id: dict[uuid.UUID, BM25Winner]


def _embedding_tiebreak_key(embedding: Embedding) -> tuple[str, int, str]:
    """Deterministic secondary key for equal-score ordering."""
    meta = embedding.metadata_json or {}
    chunk_index = meta.get("chunk_index", -1)
    if not isinstance(chunk_index, int):
        chunk_index = -1
    return (str(embedding.document_id), chunk_index, str(embedding.id))


def _sort_scored_embeddings(
    scored: list[tuple[Embedding, float]],
) -> list[tuple[Embedding, float]]:
    """Sort DESC by score with a deterministic tie-breaker."""
    return sorted(
        scored,
        key=lambda item: (-item[1], _embedding_tiebreak_key(item[0])),
    )


def _variant_mode_for_count(count: int) -> VariantMode:
    return "multi" if count > 1 else "single"


def build_variant_trace_metadata(bundle: SearchResultBundle) -> dict[str, object]:
    """Compact trace metadata used on parent request traces."""
    return {
        "variant_mode": bundle.variant_mode,
        "query_variant_count": bundle.query_variant_count,
        "extra_embedded_queries": bundle.extra_embedded_queries,
        "extra_embedding_api_requests": bundle.extra_embedding_api_requests,
        "extra_vector_search_calls": bundle.extra_vector_search_calls,
        "bm25_expansion_mode": bundle.bm25_expansion_mode,
        "bm25_query_variant_count": bundle.bm25_query_variant_count,
        "bm25_variant_eval_count": bundle.bm25_variant_eval_count,
        "extra_bm25_variant_evals": bundle.extra_bm25_variant_evals,
        "bm25_merged_hit_count_before_cap": bundle.bm25_merged_hit_count_before_cap,
        "bm25_merged_hit_count_after_cap": bundle.bm25_merged_hit_count_after_cap,
        "retrieval_duration_ms": bundle.retrieval_duration_ms,
    }


def build_variant_trace_tag(variant_mode: VariantMode) -> str:
    """Simple tag for slicing traces by variant fan-out."""
    return f"variants:{variant_mode}"


def detect_query_script_bucket(text: str) -> str:
    """Detect a coarse script bucket from the query text."""
    if re.search(r"[а-яё]", text.casefold(), flags=re.UNICODE):  # noqa: RUF001
        return "cyrillic"
    if re.search(r"[a-z]", text.casefold(), flags=re.UNICODE):
        return "latin"
    return "other"


def _embedding_script_bucket(embedding: Embedding) -> str:
    """Infer a coarse script bucket from embedding metadata or chunk text."""
    meta = embedding.metadata_json or {}
    language = meta.get("language")
    if isinstance(language, str) and language.strip():
        lowered = language.strip().lower()
        if lowered.startswith(CYRILLIC_LANGUAGE_PREFIXES):
            return "cyrillic"
        if lowered.startswith(LATIN_LANGUAGE_PREFIXES):
            return "latin"
    return detect_query_script_bucket(embedding.chunk_text or "")


def expand_query(query: str) -> list[str]:
    """Generate lightweight query variants without changing user intent."""
    variants: list[str] = []

    def _push(value: str) -> None:
        variants[:] = _normalize_query_variants([*variants, value])

    _push(query)

    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    _push(cleaned)

    tokens = re.findall(r"\w+", query.casefold(), flags=re.UNICODE)
    if tokens:
        unique_tokens = list(dict.fromkeys(tokens))
        _push(" ".join(unique_tokens))

    return variants or [query]


def _normalize_query_variants(values: list[str]) -> list[str]:
    """Normalize and dedupe query variants while preserving first-seen order."""
    variants: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        variants.append(normalized)
    return variants


def lexical_safe_query_variants(
    query: str,
    *,
    base_variants: list[str] | None = None,
) -> list[str]:
    """
    Return only normalization-safe variants suitable for lexical BM25 scoring.

    Today this mirrors the deterministic normalized variants used for vector
    retrieval. If expand_query() ever grows to include freer rewrites or
    paraphrases, BM25 must continue consuming only the lexical-safe subset
    unless the lexical branch contract is explicitly revisited.
    """
    source_variants = base_variants if base_variants is not None else expand_query(query)
    variants = _normalize_query_variants(source_variants)
    return variants or [query]


def embed_query(
    query: str,
    *,
    api_key: str,
    timeout: float | None = None,
) -> list[float]:
    """
    Embed a search query using OpenAI embeddings API.

    Args:
        query: Text to embed.
        api_key: OpenAI API key.
        timeout: Optional HTTP timeout (seconds); defaults to global OpenAI timeout.

    Returns:
        1536-dimensional embedding vector.
    """
    openai_client = get_openai_client(api_key, timeout=timeout)
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    return response.data[0].embedding


def embed_queries(
    queries: list[str],
    *,
    api_key: str,
    timeout: float | None = None,
) -> list[list[float]]:
    """Embed multiple search queries in one OpenAI API round-trip."""
    if not queries:
        return []
    openai_client = get_openai_client(api_key, timeout=timeout)
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=queries,
    )
    return [item.embedding for item in response.data]


def embed_queries_with_stats(
    queries: list[str], *, api_key: str
) -> tuple[list[list[float]], int]:
    """Embed multiple queries and return the actual API request count used."""
    if not queries:
        return [], 0
    vectors = embed_queries(queries, api_key=api_key)
    return vectors, 1


def _bm25_score_candidates_with_signal(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> tuple[list[tuple[Embedding, float]], bool]:
    """
    BM25 scoring over a pre-loaded list of Embedding objects.
    No DB access — operates on objects already in memory.
    Returns normalized scores in [0, 1].
    """
    prepared_corpus = _prepare_bm25_corpus(candidates)
    scored = _score_prepared_bm25_corpus(prepared_corpus, query, top_k)
    return scored, _has_lexical_signal(scored, query, top_k)


def _bm25_score_candidates(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    scored, _ = _bm25_score_candidates_with_signal(candidates, query, top_k)
    return scored


def _build_vector_candidate_set(
    client_id: uuid.UUID,
    variant_vectors: list[list[float]],
    db: Session,
    *,
    vector_search_fn,
) -> VectorCandidateSet:
    """Acquire, merge, dedupe, and truncate vector candidates across variants."""
    vector_started_at = perf_counter()
    vector_candidate_map: dict[uuid.UUID, tuple[Embedding, float]] = {}
    vector_search_call_count = 0
    for variant_vector in variant_vectors:
        vector_search_call_count += 1
        for embedding, similarity in vector_search_fn(
            client_id,
            variant_vector,
            BM25_CANDIDATE_POOL,
            db,
        ):
            existing = vector_candidate_map.get(embedding.id)
            if existing is None or similarity > existing[1]:
                vector_candidate_map[embedding.id] = (embedding, similarity)
    return VectorCandidateSet(
        candidates=_sort_scored_embeddings(list(vector_candidate_map.values()))[
            :BM25_CANDIDATE_POOL
        ],
        call_count=vector_search_call_count,
        duration_ms=round((perf_counter() - vector_started_at) * 1000, 2),
    )


def bm25_search_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    BM25 full-text search over chunk_text for a client.
    Fetches all client chunks from DB, then delegates scoring to _bm25_score_candidates.
    Public API preserved for direct use and tests.
    """
    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .filter(Embedding.chunk_text.isnot(None))
        .all()
    )
    return _bm25_score_candidates(embeddings, query, top_k)


def _prepare_bm25_corpus(candidates: list[Embedding]) -> PreparedBM25Corpus:
    """Build the shared in-memory BM25 scorer once for a candidate pool."""
    if not candidates:
        return PreparedBM25Corpus(candidates=[], scorer=None)
    corpus = [(emb.chunk_text or "").lower().split() for emb in candidates]
    return PreparedBM25Corpus(candidates=candidates, scorer=BM25Okapi(corpus))


def _lexical_overlap_results(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Current lexical branch participation criteria over a ranked output list."""
    lexical_overlap_scored = [
        (embedding, _lexical_overlap_score(query, embedding.chunk_text or ""))
        for embedding in candidates
    ]
    lexical_overlap_scored = [
        (embedding, score)
        for embedding, score in lexical_overlap_scored
        if score > 0.0
    ]
    return _sort_scored_embeddings(lexical_overlap_scored)[:top_k]


def _normalize_scored_results(
    scored: list[tuple[Embedding, float]],
) -> list[tuple[Embedding, float]]:
    """Normalize descending scores into [0, 1] while preserving ordering."""
    if not scored:
        return []
    max_s = scored[0][1]
    min_s = scored[-1][1]
    if max_s == min_s:
        return [(emb, 1.0) for emb, _ in scored]
    return [(emb, (s - min_s) / (max_s - min_s)) for emb, s in scored]


def _score_prepared_bm25_corpus(
    prepared_corpus: PreparedBM25Corpus,
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """
    BM25 scoring over a shared in-memory corpus.

    One corpus is built per request-stage candidate pool; repeated variant
    evaluation is only repeated lexical scoring over that already-built corpus.
    """
    query_tokens = query.lower().split()
    if not query_tokens or not prepared_corpus.candidates or prepared_corpus.scorer is None:
        return []

    raw_scores = [float(score) for score in prepared_corpus.scorer.get_scores(query_tokens)]
    scored = _sort_scored_embeddings(list(zip(prepared_corpus.candidates, raw_scores, strict=True)))[:top_k]
    if not scored:
        return []

    distinct_raw_scores = len({round(score, 12) for _, score in scored}) > 1
    if not distinct_raw_scores:
        scored = _lexical_overlap_results(prepared_corpus.candidates, query, top_k)
        if not scored:
            return []

    return _normalize_scored_results(scored)


def _has_lexical_signal(
    results: list[tuple[Embedding, float]],
    query: str,
    top_k: int,
) -> bool:
    """
    Preserve lexical participation semantics over the final lexical branch output.

    Symmetric BM25 expansion changes lexical input generation only. This signal
    must be derived from the final merged lexical list handed downstream, not
    from a raw OR across per-variant scoring attempts.
    """
    return bool(_lexical_overlap_results([embedding for embedding, _ in results], query, top_k))


def _resolve_bm25_expansion_mode() -> BM25ExpansionMode:
    """Return the effective BM25 lexical expansion mode with a safe default."""
    if settings.bm25_expansion_mode == "symmetric_variants":
        return "symmetric_variants"
    return "asymmetric"


def _format_bm25_trace_results(
    results: list[tuple[Embedding, float]],
    *,
    winner_by_id: dict[uuid.UUID, BM25Winner],
) -> list[dict[str, object]]:
    """Add compact winner provenance to BM25 trace payloads."""
    payload = format_embedding_results(results, score_name="bm25_score")
    for (embedding, _), item in zip(results, payload, strict=True):
        winner = winner_by_id.get(embedding.id)
        if winner is None:
            continue
        item["winner_variant_index"] = winner.variant_index
        if len(winner.variant_query) <= BM25_DEBUG_VARIANT_TEXT_MAX_LEN:
            item["winner_variant_text"] = truncate_text(winner.variant_query)
    return payload


def _run_bm25_search(
    candidates: list[Embedding],
    *,
    query: str,
    variant_queries: list[str],
    top_k: int,
    expansion_mode: BM25ExpansionMode,
) -> BM25SearchBundle:
    """Evaluate BM25 over one shared corpus using asymmetric or symmetric policy."""
    prepared_corpus = _prepare_bm25_corpus(candidates)
    variant_eval_count = len(variant_queries)
    if not candidates or not variant_queries:
        return BM25SearchBundle(
            results=[],
            has_lexical_signal=False,
            variant_queries=variant_queries or [query],
            variant_eval_count=0,
            merged_hit_count_before_cap=0,
            merged_hit_count_after_cap=0,
            winner_by_id={},
        )

    if expansion_mode == "asymmetric":
        results = _score_prepared_bm25_corpus(prepared_corpus, query, top_k)
        winner_by_id = {
            embedding.id: BM25Winner(variant_index=0, variant_query=query, score=score)
            for embedding, score in results
        }
        return BM25SearchBundle(
            results=results,
            has_lexical_signal=_has_lexical_signal(results, query, top_k),
            variant_queries=variant_queries,
            variant_eval_count=variant_eval_count,
            merged_hit_count_before_cap=len(results),
            merged_hit_count_after_cap=len(results),
            winner_by_id=winner_by_id,
        )

    merged_by_id: dict[uuid.UUID, tuple[Embedding, BM25Winner]] = {}
    for variant_index, variant_query in enumerate(variant_queries):
        variant_results = _score_prepared_bm25_corpus(prepared_corpus, variant_query, top_k)
        for embedding, score in variant_results:
            existing = merged_by_id.get(embedding.id)
            if existing is None or score > existing[1].score:
                merged_by_id[embedding.id] = (
                    embedding,
                    BM25Winner(
                        variant_index=variant_index,
                        variant_query=variant_query,
                        score=score,
                    ),
                )

    merged_results = _sort_scored_embeddings(
        [(embedding, winner.score) for embedding, winner in merged_by_id.values()]
    )
    merged_hit_count_before_cap = len(merged_results)
    final_results = merged_results[:top_k]
    winner_by_id = {
        embedding.id: merged_by_id[embedding.id][1]
        for embedding, _ in final_results
        if embedding.id in merged_by_id
    }
    return BM25SearchBundle(
        results=final_results,
        has_lexical_signal=_has_lexical_signal(final_results, query, top_k),
        variant_queries=variant_queries,
        variant_eval_count=variant_eval_count,
        merged_hit_count_before_cap=merged_hit_count_before_cap,
        merged_hit_count_after_cap=len(final_results),
        winner_by_id=winner_by_id,
    )


def reciprocal_rank_fusion(
    vector_results: list[tuple[Embedding, float]],
    bm25_results: list[tuple[Embedding, float]],
    k: int = 60,
    top_k: int = 5,
) -> list[tuple[Embedding, float]]:
    """Combine vector and BM25 results using Reciprocal Rank Fusion."""
    scores: dict[uuid.UUID, float] = {}
    id_to_emb: dict[uuid.UUID, Embedding] = {}

    for rank, (emb, _) in enumerate(vector_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    for rank, (emb, _) in enumerate(bm25_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    sorted_ids = sorted(
        scores.keys(),
        key=lambda id_: (-scores[id_], _embedding_tiebreak_key(id_to_emb[id_])),
    )
    return [(id_to_emb[id_], scores[id_]) for id_ in sorted_ids[:top_k]]


def _collect_score_map(results: list[tuple[Embedding, float]]) -> dict[uuid.UUID, float]:
    """Collect the strongest score per embedding id."""
    score_map: dict[uuid.UUID, float] = {}
    for embedding, score in results:
        existing = score_map.get(embedding.id)
        if existing is None or score > existing:
            score_map[embedding.id] = score
    return score_map


def _lexical_overlap_score(query: str, chunk_text: str) -> float:
    """Cheap lexical signal used as an interim reranker until a cross-encoder is added."""
    query_tokens = set(re.findall(r"\w+", query.casefold(), flags=re.UNICODE))
    if not query_tokens:
        return 0.0
    chunk_tokens = set(re.findall(r"\w+", (chunk_text or "").casefold(), flags=re.UNICODE))
    if not chunk_tokens:
        return 0.0
    overlap = len(query_tokens & chunk_tokens)
    return overlap / len(query_tokens)


def rerank_candidates(
    query: str,
    candidates: list[tuple[Embedding, float]],
    *,
    vector_scores: dict[uuid.UUID, float] | None = None,
    bm25_scores: dict[uuid.UUID, float] | None = None,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Apply an interim heuristic reranking stage over fused candidates."""
    if not candidates:
        return []

    max_rrf = max(score for _, score in candidates)
    vector_scores = vector_scores or {}
    bm25_scores = bm25_scores or {}

    rescored: list[tuple[Embedding, float]] = []
    for embedding, rrf_score in candidates:
        lexical_score = _lexical_overlap_score(query, embedding.chunk_text or "")
        vector_score = vector_scores.get(embedding.id, 0.0)
        bm25_score = bm25_scores.get(embedding.id, 0.0)
        normalized_rrf = rrf_score / max_rrf if max_rrf else 0.0
        final_score = (
            (lexical_score * RERANK_LEXICAL_WEIGHT)
            + (vector_score * RERANK_VECTOR_WEIGHT)
            + (bm25_score * RERANK_BM25_WEIGHT)
            + (normalized_rrf * RERANK_RRF_WEIGHT)
        )
        rescored.append((embedding, round(final_score, 6)))

    rescored = sorted(
        rescored,
        key=lambda item: (
            -item[1],
            -vector_scores.get(item[0].id, 0.0),
            -bm25_scores.get(item[0].id, 0.0),
            _embedding_tiebreak_key(item[0]),
        ),
    )
    return rescored[:top_k]


def apply_script_boost(
    query_script_bucket: str,
    candidates: list[tuple[Embedding, float]],
    *,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Soft-boost chunks that match the query script bucket."""
    boosted: list[tuple[Embedding, float]] = []
    for embedding, score in candidates:
        adjusted = score + (
            SCRIPT_BOOST_FACTOR
            if _embedding_script_bucket(embedding) == query_script_bucket
            else 0.0
        )
        boosted.append((embedding, round(adjusted, 6)))
    boosted = _sort_scored_embeddings(boosted)
    return boosted[:top_k]


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def _candidate_similarity(first: Embedding, second: Embedding) -> float:
    """Approximate chunk similarity using Jaccard overlap."""
    first_tokens = _token_set(first.chunk_text or "")
    second_tokens = _token_set(second.chunk_text or "")
    if not first_tokens or not second_tokens:
        return 0.0
    union = first_tokens | second_tokens
    return len(first_tokens & second_tokens) / len(union)


def mmr_select(
    candidates: list[tuple[Embedding, float]],
    *,
    top_k: int,
    lambda_mult: float = MMR_LAMBDA,
) -> MMRSelectionResult:
    """
    Select top-k diverse chunks while preserving comparable output scores.

    This is an interim heuristic over a small post-rerank pool. Similarity is
    lexical Jaccard overlap on token sets, and each selection step recomputes
    pairwise comparisons against already-selected chunks. That is acceptable for
    the current bounded usage (typically 6-10 candidates, still reasonable up to
    roughly 50), but pools approaching 100 candidates become a hot-path cost and
    should be capped or optimized before we widen them further.
    """
    if not candidates:
        return MMRSelectionResult(results=[], replacements=[], diagnostics=[])
    if len(candidates) < top_k:
        logger.warning(
            "MMR received fewer candidates than requested top_k",
            extra={"candidate_count": len(candidates), "top_k": top_k},
        )

    selected: list[tuple[Embedding, float]] = []
    selected_ids: set[uuid.UUID] = set()
    replacements: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    baseline_top_ids = {embedding.id for embedding, _ in candidates[:top_k]}
    baseline_top_order = [embedding.id for embedding, _ in candidates[:top_k]]
    baseline_top_map = {embedding.id: embedding for embedding, _ in candidates[:top_k]}
    displaced_baseline_ids: set[uuid.UUID] = set()
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        if not selected:
            chosen = remaining.pop(0)
            selected.append(chosen)
            selected_ids.add(chosen[0].id)
            diagnostics.append(
                {
                    "selected_chunk_id": str(chosen[0].id),
                    "selected_rank": 1,
                    "base_score": round(chosen[1], 6),
                    "mmr_score": round(chosen[1], 6),
                    "redundancy_penalty": 0.0,
                }
            )
            continue

        best_index = 0
        best_score = float("-inf")
        best_similarity = 0.0
        for index, (embedding, relevance) in enumerate(remaining):
            similarity = max(
                _candidate_similarity(embedding, chosen_embedding)
                for chosen_embedding, _ in selected
            )
            mmr_score = (lambda_mult * relevance) - ((1 - lambda_mult) * similarity)
            if mmr_score > best_score:
                best_score = mmr_score
                best_index = index
                best_similarity = similarity

        chosen = remaining.pop(best_index)
        selected_snapshot = list(selected)
        selected.append((chosen[0], round(chosen[1], 6)))
        selected_ids.add(chosen[0].id)
        diagnostics.append(
            {
                "selected_chunk_id": str(chosen[0].id),
                "selected_rank": len(selected),
                "base_score": round(chosen[1], 6),
                "mmr_score": round(best_score, 6),
                "redundancy_penalty": round(best_similarity, 6),
            }
        )

        if chosen[0].id not in baseline_top_ids:
            for baseline_id in baseline_top_order:
                if baseline_id not in selected_ids and baseline_id not in displaced_baseline_ids:
                    removed_embedding = baseline_top_map[baseline_id]
                    removed_similarity = max(
                        _candidate_similarity(removed_embedding, selected_embedding)
                        for selected_embedding, _ in selected_snapshot
                    )
                    displaced_baseline_ids.add(baseline_id)
                    replacements.append(
                        {
                            "removed_chunk_id": str(baseline_id),
                            "replacement_chunk_id": str(chosen[0].id),
                            "reason": f"removed_baseline_redundancy:{removed_similarity:.3f}",
                            "removed_redundancy": round(removed_similarity, 6),
                            "replacement_redundancy": round(best_similarity, 6),
                        }
                    )
                    break

    return MMRSelectionResult(
        results=selected,
        replacements=replacements,
        diagnostics=diagnostics,
    )


def detect_source_overlaps(
    candidates: list[tuple[Embedding, float]],
    *,
    similarity_threshold: float = 0.75,
) -> tuple[bool, tuple[SourceOverlapPair, ...]]:
    """Detect cross-document overlap on the final top-k result set only."""
    if len(candidates) > MAX_OVERLAP_CHECK_CANDIDATES:
        logger.warning(
            "Source overlap detection received more candidates than expected; truncating",
            extra={
                "candidate_count": len(candidates),
                "max_candidates": MAX_OVERLAP_CHECK_CANDIDATES,
            },
        )
    bounded_candidates = candidates[:MAX_OVERLAP_CHECK_CANDIDATES]
    overlap_pairs: list[SourceOverlapPair] = []
    for index, (first, _) in enumerate(bounded_candidates):
        for second, _ in bounded_candidates[index + 1 :]:
            if first.document_id == second.document_id:
                continue
            similarity = _candidate_similarity(first, second)
            if similarity < similarity_threshold:
                continue
            overlap_pairs.append(
                SourceOverlapPair(
                    chunk_a_id=str(first.id),
                    chunk_b_id=str(second.id),
                    similarity=round(similarity, 4),
                )
            )
    return bool(overlap_pairs), tuple(overlap_pairs)


def _pgvector_search(
    client_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """Native pgvector cosine distance search. PostgreSQL only."""
    try:
        distance_expr = Embedding.vector.cosine_distance(query_vector)
        results_with_distance = (
            db.query(Embedding, distance_expr.label("distance"))
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.client_id == client_id)
            .filter(Embedding.vector.isnot(None))
            .order_by(distance_expr)
            .limit(top_k)
            .all()
        )
        return [
            (emb, max(0.0, 1.0 - distance))
            for emb, distance in results_with_distance
        ]
    except Exception:
        logger.exception("pgvector search failed; falling back to Python cosine search")
        return _python_cosine_search(client_id, query_vector, top_k, db)


def search_similar_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """Compatibility wrapper returning ranked results only."""
    return search_similar_chunks_detailed(
        client_id=client_id,
        query=query,
        top_k=top_k,
        db=db,
        api_key=api_key,
    ).results


def search_similar_chunks_detailed(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
    trace: TraceHandle | None = None,
    precomputed_query_variants: list[str] | None = None,
    precomputed_variant_vectors: list[list[float]] | None = None,
    precomputed_embedding_api_request_count: int | None = None,
) -> SearchResultBundle:
    """
    Hybrid search: pgvector cosine similarity + BM25, merged with RRF.

    PostgreSQL uses pgvector for candidate acquisition, while SQLite uses
    Python cosine search. Downstream ranking and observability stages are shared.
    """
    retrieval_started_at = perf_counter()

    use_precomputed = (
        precomputed_query_variants is not None
        and precomputed_variant_vectors is not None
        and precomputed_query_variants
        and len(precomputed_query_variants) == len(precomputed_variant_vectors)
    )

    query_variants = precomputed_query_variants if use_precomputed else expand_query(query)
    query_variant_count = len(query_variants)
    variant_mode = _variant_mode_for_count(query_variant_count)
    extra_variant_count = max(query_variant_count - 1, 0)
    if trace is not None:
        trace.span(
            name="query-expansion",
            input={"query": query},
        ).end(
            output={
                "variants": query_variants,
                "query_variant_count": query_variant_count,
                "variant_mode": variant_mode,
                "extra_variant_count": extra_variant_count,
            }
        )

    if use_precomputed:
        variant_vectors = precomputed_variant_vectors or []
        embedding_api_request_count = int(precomputed_embedding_api_request_count or 1)
        query_embedding_duration_ms = 0.0
        embedded_query_count = len(variant_vectors)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []
        # Intentionally skip `query-embedding` span: embeddings were computed upstream.
    else:
        embedding_started_at = perf_counter()
        variant_vectors, embedding_api_request_count = embed_queries_with_stats(
            query_variants,
            api_key=api_key,
        )
        query_embedding_duration_ms = round((perf_counter() - embedding_started_at) * 1000, 2)
        embedded_query_count = len(query_variants)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []
    query_script_bucket = detect_query_script_bucket(query)
    if trace is not None and not use_precomputed:
        trace.span(
            name="query-embedding",
            input={
                "query_variants": query_variants,
                "query_variant_count": query_variant_count,
                "variant_mode": variant_mode,
                "model": EMBEDDING_MODEL,
            },
        ).end(
            output={
                "embedded_query_count": embedded_query_count,
                "extra_embedded_queries": extra_embedded_queries,
                "embedding_api_request_count": embedding_api_request_count,
                "extra_embedding_api_requests": extra_embedding_api_requests,
                "duration_ms": query_embedding_duration_ms,
            }
        )

    db_url = str(db.bind.url if db.bind else "")
    vector_engine = "python-cosine" if "sqlite" in db_url else "pgvector"
    vector_search_fn = _python_cosine_search if "sqlite" in db_url else _pgvector_search
    bm25_expansion_mode = _resolve_bm25_expansion_mode()

    # Build one shared candidate set before lexical stages: engine-specific
    # acquisition, then cross-variant merge/dedup/truncation.
    vector_candidate_set = _build_vector_candidate_set(
        client_id,
        variant_vectors,
        db,
        vector_search_fn=vector_search_fn,
    )
    vector_candidates = vector_candidate_set.candidates
    vector_search_call_count = vector_candidate_set.call_count
    vector_duration_ms = vector_candidate_set.duration_ms
    extra_vector_search_calls = max(vector_search_call_count - 1, 0)
    bm25_variant_queries = (
        [query]
        if bm25_expansion_mode == "asymmetric"
        else lexical_safe_query_variants(query, base_variants=query_variants)
    )

    if not vector_candidates:
        retrieval_duration_ms = round((perf_counter() - retrieval_started_at) * 1000, 2)
        if trace is not None:
            trace.span(
                name="vector-search",
                input={
                    "query_embedding": format_query_embedding_preview(trace_query_vector),
                    "query_variants": query_variants,
                    "client_id": str(client_id),
                    "top_k": BM25_CANDIDATE_POOL,
                    "engine": vector_engine,
                },
            ).end(
                output={
                    "chunks": [],
                    "duration_ms": vector_duration_ms,
                    "total_candidates_scanned": 0,
                    "vector_search_call_count": vector_search_call_count,
                    "extra_vector_search_calls": extra_vector_search_calls,
                }
            )
        return SearchResultBundle(
            results=[],
            query_variants=query_variants,
            query_script_bucket=query_script_bucket,
            reliability=build_reliability_assessment(
                top_score=None,
                result_count=0,
            ),
            query_variant_count=query_variant_count,
            variant_mode=variant_mode,
            extra_variant_count=extra_variant_count,
            embedded_query_count=embedded_query_count,
            extra_embedded_queries=extra_embedded_queries,
            embedding_api_request_count=embedding_api_request_count,
            extra_embedding_api_requests=extra_embedding_api_requests,
            vector_search_call_count=vector_search_call_count,
            extra_vector_search_calls=extra_vector_search_calls,
            bm25_expansion_mode=bm25_expansion_mode,
            bm25_query_variant_count=len(bm25_variant_queries),
            bm25_variant_eval_count=0,
            extra_bm25_variant_evals=0,
            retrieval_duration_ms=retrieval_duration_ms,
            query_embedding_duration_ms=query_embedding_duration_ms,
            vector_search_duration_ms=vector_duration_ms,
        )

    vector_embs = [emb for emb, _ in vector_candidates]
    if trace is not None:
        trace.span(
            name="vector-search",
            input={
                "query_embedding": format_query_embedding_preview(trace_query_vector),
                "query_variants": query_variants,
                "client_id": str(client_id),
                "top_k": BM25_CANDIDATE_POOL,
                "engine": vector_engine,
            },
        ).end(
            output={
                "chunks": format_embedding_results(
                    vector_candidates[:top_k * 2],
                    score_name="similarity_score",
                ),
                "duration_ms": vector_duration_ms,
                "total_candidates_scanned": len(vector_candidates),
                "vector_search_call_count": vector_search_call_count,
                "extra_vector_search_calls": extra_vector_search_calls,
            }
            )

    rrf_candidate_pool = top_k * RRF_CANDIDATE_POOL_MULTIPLIER
    bm25_started_at = perf_counter()
    bm25_bundle = _run_bm25_search(
        vector_embs,
        query=query,
        variant_queries=bm25_variant_queries,
        top_k=rrf_candidate_pool,
        expansion_mode=bm25_expansion_mode,
    )
    bm25_results = bm25_bundle.results
    has_lexical_signal = bm25_bundle.has_lexical_signal
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={
                "query": query,
                "query_variants": bm25_bundle.variant_queries,
                "client_id": str(client_id),
                "top_k": rrf_candidate_pool,
                "bm25_expansion_mode": bm25_expansion_mode,
                "variant_source": (
                    "original-query"
                    if bm25_expansion_mode == "asymmetric"
                    else "lexical-safe-normalized-variants"
                ),
            },
        ).end(
            output={
                "chunks": _format_bm25_trace_results(
                    bm25_results,
                    winner_by_id=bm25_bundle.winner_by_id,
                ),
                "duration_ms": bm25_duration_ms,
                "bm25_query_variant_count": len(bm25_bundle.variant_queries),
                "bm25_variant_eval_count": bm25_bundle.variant_eval_count,
                "extra_bm25_variant_evals": max(bm25_bundle.variant_eval_count - 1, 0),
                "bm25_merged_hit_count_before_cap": (
                    bm25_bundle.merged_hit_count_before_cap
                ),
                "bm25_merged_hit_count_after_cap": (
                    bm25_bundle.merged_hit_count_after_cap
                ),
            }
        )
    vector_for_rrf = vector_candidates[:rrf_candidate_pool]
    best_vector_similarity = vector_candidates[0][1] if vector_candidates else None
    best_keyword_score = bm25_results[0][1] if bm25_results else None

    rrf_started_at = perf_counter()
    fused_results = reciprocal_rank_fusion(
        vector_for_rrf,
        bm25_results,
        top_k=rrf_candidate_pool,
    )
    rrf_duration_ms = round((perf_counter() - rrf_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="rrf-fusion",
            input={
                "vector_results": format_embedding_results(
                    vector_for_rrf,
                    score_name="similarity_score",
                ),
                "bm25_results": format_embedding_results(
                    bm25_results,
                    score_name="bm25_score",
                ),
                "bm25_expansion_mode": bm25_expansion_mode,
            },
        ).end(
            output={
                "merged_chunks": format_embedding_results(
                    fused_results,
                    score_name="rrf_score",
                ),
                "duration_ms": rrf_duration_ms,
            }
        )

    rerank_started_at = perf_counter()
    reranked_results = rerank_candidates(
        query,
        fused_results,
        vector_scores=_collect_score_map(vector_candidates),
        bm25_scores=_collect_score_map(bm25_results),
        top_k=top_k,
    )
    if trace is not None:
        trace.span(
            name="reranking",
            input={
                "query": query,
                "candidate_count": len(fused_results),
                "model": "heuristic-rrf-v0",
            },
        ).end(
            output={
                "ranked": format_embedding_results(
                    reranked_results,
                    score_name="reranker_score",
                ),
                "top_score": reranked_results[0][1] if reranked_results else None,
                "duration_ms": round((perf_counter() - rerank_started_at) * 1000, 2),
            }
        )

    script_started_at = perf_counter()
    script_boosted_results = apply_script_boost(
        query_script_bucket,
        reranked_results,
        top_k=top_k * 2,
    )
    if trace is not None:
        trace.span(
            name="script-boost",
            input={
                "query_script_bucket": query_script_bucket,
                "candidate_count": len(reranked_results),
                "strategy": "coarse-script-bucket-heuristic",
            },
        ).end(
            output={
                "reordered": format_embedding_results(
                    script_boosted_results[:top_k],
                    score_name="script_boost_score",
                ),
                "duration_ms": round((perf_counter() - script_started_at) * 1000, 2),
            }
        )

    # Keep MMR on the small post-rerank pool only. The current lexical pairwise
    # similarity is an interim heuristic, not a large-pool reranker.
    mmr_started_at = perf_counter()
    mmr_selection = mmr_select(
        script_boosted_results,
        top_k=top_k,
    )
    final_results = mmr_selection.results
    vector_similarity_by_id = {emb.id: sim for emb, sim in vector_candidates}
    vector_similarities: list[float | None] = [
        float(vector_similarity_by_id[emb.id]) if emb.id in vector_similarity_by_id else None
        for emb, _ in final_results
    ]
    if trace is not None:
        trace.span(
            name="mmr-pass",
            input={
                "lambda": MMR_LAMBDA,
                "candidate_count": len(script_boosted_results),
                "selection_strategy": "mmr-order-base-score-output",
            },
        ).end(
            output={
                "final_chunks": format_embedding_results(
                    final_results,
                    score_name="final_score",
                ),
                "selection_diagnostics": mmr_selection.diagnostics,
                "replacements": mmr_selection.replacements,
                "duration_ms": round((perf_counter() - mmr_started_at) * 1000, 2),
            }
        )

    overlap_started_at = perf_counter()
    source_overlap_detected, source_overlap_pairs = detect_source_overlaps(
        final_results
    )
    contradiction_pairs = detect_metadata_contradictions(
        final_results,
        source_overlap_pairs,
    )
    client_row: Client | None = None
    if settings.contradiction_adjudication_enabled and hasattr(db, "query"):
        client_row = db.query(Client).filter(Client.id == client_id).first()
    contradiction_adjudication, contradiction_adjudication_observability = (
        _build_contradiction_adjudication_evidence(
            contradiction_pairs=contradiction_pairs,
            final_results=final_results,
            client=client_row,
            api_key=api_key,
        )
    )
    reliability = build_reliability_assessment(
        top_score=final_results[0][1] if final_results else None,
        result_count=len(final_results),
        source_overlap_detected=source_overlap_detected,
        source_overlap_pairs=source_overlap_pairs,
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=contradiction_pairs,
        contradiction_adjudication=contradiction_adjudication,
        contradiction_adjudication_observability=contradiction_adjudication_observability,
    )
    if trace is not None:
        # The historical span name is preserved for continuity; payload semantics are overlap-only.
        trace.span(
            name="source-overlap-check",
            input={
                "candidate_count": len(final_results),
                "strategy": "cross-document-jaccard-overlap-heuristic",
            },
        ).end(
            output={
                **build_reliability_projection(reliability),
                "duration_ms": round((perf_counter() - overlap_started_at) * 1000, 2),
            }
        )
    retrieval_duration_ms = round((perf_counter() - retrieval_started_at) * 1000, 2)

    return SearchResultBundle(
        results=final_results,
        best_vector_similarity=best_vector_similarity,
        vector_similarities=vector_similarities,
        best_keyword_score=best_keyword_score,
        has_lexical_signal=has_lexical_signal,
        query_variants=query_variants,
        query_script_bucket=query_script_bucket,
        reliability=reliability,
        query_variant_count=query_variant_count,
        variant_mode=variant_mode,
        extra_variant_count=extra_variant_count,
        embedded_query_count=embedded_query_count,
        extra_embedded_queries=extra_embedded_queries,
        embedding_api_request_count=embedding_api_request_count,
        extra_embedding_api_requests=extra_embedding_api_requests,
        vector_search_call_count=vector_search_call_count,
        extra_vector_search_calls=extra_vector_search_calls,
        bm25_expansion_mode=bm25_expansion_mode,
        bm25_query_variant_count=len(bm25_bundle.variant_queries),
        bm25_variant_eval_count=bm25_bundle.variant_eval_count,
        extra_bm25_variant_evals=max(bm25_bundle.variant_eval_count - 1, 0),
        bm25_merged_hit_count_before_cap=bm25_bundle.merged_hit_count_before_cap,
        bm25_merged_hit_count_after_cap=bm25_bundle.merged_hit_count_after_cap,
        retrieval_duration_ms=retrieval_duration_ms,
        query_embedding_duration_ms=query_embedding_duration_ms,
        vector_search_duration_ms=vector_duration_ms,
    )


def _python_cosine_search(
    client_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    Fallback: Python-based cosine similarity search.

    Used for SQLite (tests) or when pgvector is not available.
    Not recommended for production with large datasets.

    Args:
        client_id: Client ID for filtering.
        query_vector: Pre-computed query embedding.
        top_k: Number of results.
        db: Database session.
    """
    import math

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        # Try Vector column first, fall back to metadata_json["vector"]
        vector = None
        if emb.vector is not None:
            vector = list(emb.vector)
        else:
            meta = emb.metadata_json or {}
            vector = meta.get("vector")

        if not vector or not isinstance(vector, list):
            continue

        # Cosine similarity
        if len(vector) != len(query_vector):
            continue
        dot = sum(a * b for a, b in zip(query_vector, vector, strict=True))
        norm1 = math.sqrt(sum(a * a for a in query_vector))
        norm2 = math.sqrt(sum(b * b for b in vector))
        if norm1 == 0 or norm2 == 0:
            continue
        sim = max(0.0, min(1.0, dot / (norm1 * norm2)))
        scored.append((emb, sim))

    return _sort_scored_embeddings(scored)[:top_k]


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    Kept for backward compatibility. Prefer pgvector native search.
    """
    import math

    if len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2, strict=False))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))
