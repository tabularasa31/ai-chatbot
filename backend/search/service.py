"""Business logic for vector similarity search."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

from rank_bm25 import BM25Okapi
from sqlalchemy import Text as SAText
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client, get_openai_client
from backend.core.openai_retry import async_call_openai_with_retry, call_openai_with_retry
from backend.knowledge.entity_extractor import extract_entities_from_query
from backend.models import Document, Embedding, Tenant
from backend.observability import TraceHandle
from backend.observability.formatters import (
    format_embedding_results,
    format_query_embedding_preview,
    truncate_text,
)
from backend.observability.metrics import capture_event
from backend.search import embedding_cache as _emb_cache
from backend.search.contradiction_adjudication import (
    ContradictionAdjudication,
    ContradictionAdjudicationCandidate,
    ContradictionAdjudicationRun,
    adjudicate_contradictions,
    build_contradiction_adjudication_run,
    serialize_contradiction_adjudication,
    serialize_contradiction_adjudication_run,
)
from backend.utils.math import cosine_similarity

# Number of vector candidates to pre-fetch before BM25 scoring.
# BM25 runs only on this pool (already in memory) — never queries all tenant chunks.
BM25_CANDIDATE_POOL = 200
# Cap for the standalone bm25_search_chunks() prefilter: bounds memory and CPU
# even when a query token matches a large fraction of a tenant's corpus.
BM25_PREFILTER_CANDIDATE_LIMIT = 1000
# Cap on unique query tokens used to build the prefilter OR-clause. Prevents
# pathological queries from generating SQL with hundreds of LIKE branches.
BM25_PREFILTER_MAX_QUERY_TOKENS = 32
# Cap for entity_overlap_search() PG candidate pull. Mirrors BM25's prefilter
# cap — a popular entity (e.g. "Pro plan" on a tenant with 10k chunks) could
# otherwise pull every row into memory before the Python intersection scoring
# step. The downstream RRF only consumes top RRF_CANDIDATE_POOL_MULTIPLIER *
# top_k anyway, so any cap >> that pool is safe.
ENTITY_SEARCH_CANDIDATE_LIMIT = 1000
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
# HTTP timeout for the query-rewrite LLM call. The effective latency cap is
# settings.openai_user_retry_budget_seconds (default 1.5s) — this value only
# matters if the retry budget is raised above it.
QUERY_REWRITE_HTTP_TIMEOUT_SECONDS = 3.0

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


def _tenant_contradiction_adjudication_enabled(tenant: Tenant | None) -> bool:
    """Resolve per-tenant contradiction adjudication override from JSON settings.

    Default is ``True`` when the setting is absent or malformed: a tenant only
    opts out by explicitly setting
    ``settings.retrieval.contradiction_adjudication.enabled = false``.
    The whole tenant-level gate is scheduled for removal — see the settings
    audit task — but until then this default keeps adjudication on for every
    tenant without manual JSON edits.
    """
    if tenant is None or not isinstance(tenant.settings, dict):
        return True
    retrieval_settings = tenant.settings.get("retrieval")
    if not isinstance(retrieval_settings, dict):
        return True
    contradiction_settings = retrieval_settings.get(
        CONTRADICTION_ADJUDICATION_SETTINGS_KEY
    )
    if not isinstance(contradiction_settings, dict):
        return True
    return contradiction_settings.get("enabled") is not False


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
    tenant: Tenant | None,
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

    if not _tenant_contradiction_adjudication_enabled(tenant):
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
    deterministic cap untouched. The global flag must be on.
    """
    if not settings.contradiction_adjudication_filter_cap_enabled:
        return False
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


def _rewrite_query_for_retrieval(query: str, *, api_key: str) -> str | None:
    """
    Rephrase a user question as English documentation-style keywords.

    Bridges the semantic gap between problem-description phrasing ("bot doesn't
    respond in Russian") and feature-name phrasing in docs ("language detection").
    Output is in English so the result can serve as the BM25 query against the
    English corpus for non-EN user queries, and also enriches vector retrieval.
    Fails silently — returns None on any error so retrieval degrades gracefully.
    """
    try:
        client = get_openai_client(api_key, timeout=QUERY_REWRITE_HTTP_TIMEOUT_SECONDS)
        response = call_openai_with_retry(
            "query_rewrite_for_retrieval",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a search query optimizer for a product knowledge base.\n"
                            "Rewrite the user's question as 3-5 English keywords or a short English noun phrase "
                            "that would appear as a topic or heading in product documentation.\n"
                            "Always output in English regardless of the input language.\n"
                            "Output only the rewritten query, nothing else."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_completion_tokens=60,
            ),
        )
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten if rewritten else None
    except Exception:
        return None


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


# ── Semantic query rewrite ────────────────────────────────────────────────────

_SEMANTIC_REWRITE_MAX_TOKENS = 40  # enough for a short keyword phrase

_SEMANTIC_REWRITE_PROMPT_PREFIX = (
    "A customer asked a product support chatbot:\n\""
)
_SEMANTIC_REWRITE_PROMPT_SUFFIX = (
    "\"\n\n"
    "Write a short technical search query (5-10 words) using product feature "
    "terminology and technical concepts that would retrieve the relevant "
    "documentation. Focus on the FEATURE or SETTING being asked about, not "
    "the user's symptom. Reply with ONLY the search query, nothing else."
)


def semantic_query_rewrite(
    query: str,
    *,
    api_key: str,
    timeout: float = 2.0,
    bot_id: str | None = None,
) -> str | None:
    """LLM-based semantic rewrite: user symptom → feature/product terminology.

    Bridges the semantic gap between how users describe problems ("bot replies
    only in Russian") and how documentation describes features ("language
    detection multilingual settings"). Language-agnostic: the LLM stays in
    the same language as the query so the multilingual embedding model can
    match chunks regardless of what language the docs are in.

    Returns None on any failure so the caller degrades gracefully to lexical
    variants only. Used for vector retrieval only, not BM25.
    """
    if not query or not api_key:
        return None
    # Concatenation instead of .format() so curly braces in user input
    # (e.g. "{name}", "{0}") don't raise KeyError / IndexError.
    prompt = _SEMANTIC_REWRITE_PROMPT_PREFIX + query + _SEMANTIC_REWRITE_PROMPT_SUFFIX
    try:
        client = get_openai_client(api_key, timeout=timeout)
        response = call_openai_with_retry(
            "semantic_query_rewrite",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=_SEMANTIC_REWRITE_MAX_TOKENS,
            ),
            bot_id=bot_id,
        )
        rewrite = (response.choices[0].message.content or "").strip()
        # Sanity: non-empty, single line, not too long
        if rewrite and "\n" not in rewrite and len(rewrite) <= 200:
            return rewrite
    except Exception:
        pass
    return None


# ── Cross-lingual retrieval helpers ──────────────────────────────────────────

# module-level caches: str(tenant_id) -> (value, monotonic_ts)
_TENANT_KB_SCRIPT_CACHE: dict[str, tuple[str | None, float]] = {}
_TENANT_KB_SCRIPTS_CACHE: dict[str, tuple[frozenset[str], float]] = {}
_TENANT_KB_SCRIPT_CACHE_TTL = 300.0  # 5 minutes
_KB_SCRIPT_SAMPLE_SIZE = 20

_SCRIPT_TO_LANGUAGE_NAME: dict[str, str] = {
    # Bucket-level approximation used when the rewrite needs to target a
    # whole script family. Per-document Document.language gives the precise
    # ISO code; this map is the coarse fallback used by the cross-lingual
    # rewrite prompt.
    "cyrillic": "Russian",
    "latin": "English",
}


def _language_to_script_bucket(language: str | None) -> str | None:
    """Map an ISO language code to a coarse script bucket (or None)."""
    if not language:
        return None
    lower = language.strip().lower()
    if lower.startswith(CYRILLIC_LANGUAGE_PREFIXES):
        return "cyrillic"
    if lower.startswith(LATIN_LANGUAGE_PREFIXES):
        return "latin"
    return None


def _kb_bucket_counts_from_languages(
    tenant_id: uuid.UUID, db: Session
) -> dict[str, int] | None:
    """Count documents by script bucket using ``Document.language``.

    Returns ``None`` when no rows have a non-null language — callers can then
    fall back to chunk sampling for backward compatibility with KBs indexed
    before parse-time language detection landed.
    """
    try:
        rows: list[tuple[str | None]] = (
            db.query(Document.language)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.language.isnot(None))
            .all()
        )
    except Exception:
        return None
    if not rows:
        return None
    counts: dict[str, int] = {}
    for (lang,) in rows:
        bucket = _language_to_script_bucket(lang)
        if bucket is None:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _kb_bucket_counts_from_chunk_sample(
    tenant_id: uuid.UUID, db: Session
) -> dict[str, int] | None:
    """Legacy fallback: sample chunk text when no Document.language is set."""
    try:
        sample: list[tuple[str]] = (
            db.query(Embedding.chunk_text)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .limit(_KB_SCRIPT_SAMPLE_SIZE)
            .all()
        )
    except Exception:
        return None
    if not sample:
        return None
    counts: dict[str, int] = {}
    for (chunk_text,) in sample:
        bucket = detect_query_script_bucket(chunk_text or "")
        if bucket in ("cyrillic", "latin"):
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _tenant_has_unlabeled_documents(
    tenant_id: uuid.UUID, db: Session
) -> bool:
    """True when at least one document for this tenant has language IS NULL.

    Used to decide whether labeled-only counts can be trusted: a partially
    labeled KB (legacy unlabeled rows + a few new uploads with language set)
    must still consider the unlabeled half, otherwise a single new EN doc
    on top of 100 legacy RU docs misclassifies the KB as Latin-only.
    """
    try:
        return (
            db.query(Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.language.is_(None))
            .limit(1)
            .first()
            is not None
        )
    except Exception:
        return False


def _resolve_kb_bucket_counts(
    tenant_id: uuid.UUID, db: Session
) -> dict[str, int]:
    """Return per-bucket document counts, preferring stored Document.language.

    When the KB is partially labeled (some documents still NULL after the
    add_documents_language_v1 migration), augment the labeled counts with a
    chunk sample so unlabeled legacy documents are not silently dropped.
    """
    labeled = _kb_bucket_counts_from_languages(tenant_id, db)
    if labeled is None:
        # Pure legacy KB — no documents have language stored.
        return _kb_bucket_counts_from_chunk_sample(tenant_id, db) or {}
    if not _tenant_has_unlabeled_documents(tenant_id, db):
        # Fully labeled — labeled counts are authoritative.
        return labeled
    # Partial labeling — merge with chunk sample so unlabeled docs still
    # contribute to bucket detection.
    sampled = _kb_bucket_counts_from_chunk_sample(tenant_id, db) or {}
    merged = dict(labeled)
    for bucket, count in sampled.items():
        merged[bucket] = merged.get(bucket, 0) + count
    return merged


def detect_tenant_kb_script(tenant_id: uuid.UUID, db: Session) -> str | None:
    """Return the predominant script bucket of a tenant's KB.

    Backed by ``Document.language`` written at parse time; falls back to chunk
    sampling for KBs that pre-date parse-time detection. Cached per tenant to
    avoid a DB round-trip on every chat turn. Returns None when no documents
    map to a known script bucket.
    """
    key = str(tenant_id)
    now = time.monotonic()
    cached = _TENANT_KB_SCRIPT_CACHE.get(key)
    if cached is not None and now - cached[1] < _TENANT_KB_SCRIPT_CACHE_TTL:
        return cached[0]

    counts = _resolve_kb_bucket_counts(tenant_id, db)
    result: str | None = None
    if counts:
        dominant = max(counts, key=counts.__getitem__)
        if dominant in ("cyrillic", "latin"):
            result = dominant

    _TENANT_KB_SCRIPT_CACHE[key] = (result, now)
    return result


def detect_tenant_kb_scripts(
    tenant_id: uuid.UUID, db: Session
) -> frozenset[str]:
    """Return every script bucket present in the tenant's KB.

    Mirrors :func:`detect_tenant_kb_script` but returns the full set so
    callers can issue cross-lingual rewrites for *each* KB language a query
    does not natively cover (mixed EN+RU KBs in particular).
    """
    key = str(tenant_id)
    now = time.monotonic()
    cached = _TENANT_KB_SCRIPTS_CACHE.get(key)
    if cached is not None and now - cached[1] < _TENANT_KB_SCRIPT_CACHE_TTL:
        return cached[0]

    counts = _resolve_kb_bucket_counts(tenant_id, db)
    result = frozenset(b for b in counts if b in ("cyrillic", "latin"))
    _TENANT_KB_SCRIPTS_CACHE[key] = (result, now)
    return result


def invalidate_tenant_kb_script_cache(tenant_id: uuid.UUID) -> None:
    """Drop the cached KB scripts for this tenant (call after document upload/delete)."""
    key = str(tenant_id)
    _TENANT_KB_SCRIPT_CACHE.pop(key, None)
    _TENANT_KB_SCRIPTS_CACHE.pop(key, None)


def semantic_query_rewrite_for_kb(
    query: str,
    *,
    kb_script: str,
    api_key: str,
    timeout: float = 2.0,
    bot_id: str | None = None,
) -> str | None:
    """Rewrite the user query in the language of the knowledge base.

    Used when the query language and KB language differ (e.g. English query
    against a Cyrillic corpus).  Generates a KB-language variant that is added
    to the vector query pool so embeddings match same-language chunks more
    reliably.  Fails silently — returns None on any error.
    """
    lang_name = _SCRIPT_TO_LANGUAGE_NAME.get(kb_script)
    if not lang_name or not query or not api_key:
        return None
    prompt = (
        _SEMANTIC_REWRITE_PROMPT_PREFIX
        + query
        + "\"\n\n"
        "Write a short technical search query (5-10 words) using product feature "
        "terminology and technical concepts that would retrieve the relevant "
        f"documentation. Focus on the FEATURE or SETTING being asked about, not "
        f"the user's symptom. Output ONLY in {lang_name}, regardless of the input "
        "language. Reply with ONLY the search query, nothing else."
    )
    try:
        client = get_openai_client(api_key, timeout=timeout)
        response = call_openai_with_retry(
            "semantic_query_rewrite_for_kb",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=_SEMANTIC_REWRITE_MAX_TOKENS,
            ),
            bot_id=bot_id,
        )
        rewrite = (response.choices[0].message.content or "").strip()
        if rewrite and "\n" not in rewrite and len(rewrite) <= 200:
            return rewrite
    except Exception:
        pass
    return None


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
    max_attempts: int | None = None,
) -> list[float]:
    """
    Embed a search query using OpenAI embeddings API.

    Args:
        query: Text to embed.
        api_key: OpenAI API key.
        timeout: Optional HTTP timeout (seconds); defaults to global OpenAI timeout.
        max_attempts: Override retry attempts (pass 1 to disable retries).

    Returns:
        1536-dimensional embedding vector.
    """
    cached = _emb_cache.get(query)
    if cached is not None:
        return cached
    openai_client = get_openai_client(api_key, timeout=timeout)
    response = call_openai_with_retry(
        "search_embed_query",
        lambda: openai_client.embeddings.create(
            model=settings.embedding_model,
            input=query,
        ),
        call_type="embedding",
        max_attempts=max_attempts,
    )
    vector = response.data[0].embedding
    _emb_cache.set(query, vector)
    return vector


def embed_queries(
    queries: list[str],
    *,
    api_key: str,
    timeout: float | None = None,
) -> list[list[float]]:
    """Embed multiple search queries in one OpenAI API round-trip.

    Vectors for texts already in the in-process cache are returned without
    an API call; only cache misses are sent to OpenAI as a single batch.
    """
    if not queries:
        return []
    cached_map: dict[str, list[float] | None] = {q: _emb_cache.get(q) for q in queries}
    misses = [q for q in queries if cached_map[q] is None]
    if not misses:
        return [cached_map[q] for q in queries]  # type: ignore[return-value]
    openai_client = get_openai_client(api_key, timeout=timeout)
    response = call_openai_with_retry(
        "search_embed_queries",
        lambda: openai_client.embeddings.create(
            model=settings.embedding_model,
            input=misses,
        ),
        call_type="embedding",
    )
    for text, item in zip(misses, response.data, strict=False):
        _emb_cache.set(text, item.embedding)
        cached_map[text] = item.embedding
    return [cached_map[q] for q in queries]  # type: ignore[return-value]


def embed_queries_with_stats(
    queries: list[str], *, api_key: str, timeout: float | None = None
) -> tuple[list[list[float]], int]:
    """Embed multiple queries and return the actual API request count used."""
    if not queries:
        return [], 0
    any_miss = any(_emb_cache.get(q) is None for q in queries)
    vectors = embed_queries(queries, api_key=api_key, timeout=timeout)
    return vectors, (1 if any_miss else 0)


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
    tenant_id: uuid.UUID,
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
            tenant_id,
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
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    BM25 full-text search over chunk_text for a tenant.

    Performs DB-side prefiltering: only chunks containing at least one query
    token (case-insensitive substring match) are fetched and scored. Chunks
    with no token overlap would receive a BM25 score of zero and contribute
    nothing to the ranking, so excluding them at the SQL layer keeps memory
    and CPU bounded even on large tenant corpora.
    """
    tokens = _bm25_prefilter_tokens(query)
    if not tokens:
        return []

    token_conditions = [
        func.lower(Embedding.chunk_text).like(
            f"%{_escape_like(token)}%", escape="\\"
        )
        for token in tokens
    ]
    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.tenant_id == tenant_id)
        .filter(Embedding.chunk_text.isnot(None))
        .filter(or_(*token_conditions))
        # Deterministic ordering so the limit truncates predictably and biases
        # toward recent content when a token matches more than the cap allows.
        .order_by(Embedding.created_at.desc(), Embedding.id.desc())
        .limit(BM25_PREFILTER_CANDIDATE_LIMIT)
        .all()
    )
    return _bm25_score_candidates(embeddings, query, top_k)


def entity_overlap_search(
    tenant_id: uuid.UUID,
    query_entities: list[str],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """Retrieve chunks whose ``entities`` overlap with the query's NER list.

    Step 5 of the entity-aware retrieval epic (ClickUp 86exe5pjx). The
    third RRF channel: dense and BM25 already cover semantic and lexical
    similarity; this channel surfaces chunks that name-match specific
    products / plans / error codes / endpoints — precisely the shapes
    the other two channels under-rank (dense smooths over rare tokens,
    BM25 is noisy on short codes).

    The per-chunk entity index (PR #540) is populated at ingest from
    ``extract_entities_from_passage``. The query-side NER comes from
    ``extract_entities_from_query`` (PR #537). Both use the SAME prompt
    family so surface forms are likely to align.

    Score = number of overlapping entities (cardinality of intersection).
    Comparison is case-sensitive on the surface form preserved by the
    NER prompts. Results are ordered by score desc; ties broken by
    ``created_at`` desc and ``id`` desc to match the BM25 ordering, so
    RRF's tie-break across channels is stable.

    PostgreSQL path: ``WHERE entities ?| array[...]`` against the GIN
    index (``ix_embeddings_entities_gin``, ``jsonb_ops``) prefilters
    candidates server-side. SQLite (tests) doesn't support ``?|``; we
    fall through to a Python filter over all tenant chunks. Both paths
    score the SAME way in Python after the candidate set is in memory.

    An empty ``query_entities`` short-circuits to ``[]`` — no DB hit.
    """
    if not query_entities:
        return []
    if not tenant_id:
        return []

    db_url = str(db.bind.url if db.bind else "")
    is_sqlite = "sqlite" in db_url

    if is_sqlite:
        # JSON column on SQLite: no GIN, no ?| operator. Walk all chunks
        # for the tenant and intersect in Python. Acceptable in tests
        # because the corpus is tiny; never hit in production.
        candidates = (
            db.query(Embedding)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .all()
        )
    else:
        # PG path: ?| (any-of-array) uses ix_embeddings_entities_gin
        # (jsonb_ops). Returns only rows with at least one overlapping
        # entity, so the in-memory scoring loop below scales with hits,
        # not the full table.
        #
        # ``Embedding.entities.op("?|")(...)`` goes through SQLAlchemy's
        # column expression API instead of a raw text() fragment with
        # the table name baked in — survives table aliases and refactors
        # without breakage. The right-hand side must be cast to
        # ``ARRAY(text)`` explicitly: otherwise SQLAlchemy infers the
        # JSONB column on the left and JSON-encodes the Python list
        # (``'["a","b"]'``), which Postgres rejects with "malformed
        # array literal" — the ``?|`` operator wants a real text[] array.
        candidates = (
            db.query(Embedding)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(
                Embedding.entities.op("?|")(
                    cast(list(query_entities), ARRAY(SAText()))
                )
            )
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .limit(ENTITY_SEARCH_CANDIDATE_LIMIT)
            .all()
        )

    query_set = set(query_entities)
    scored: list[tuple[Embedding, float]] = []
    for emb in candidates:
        chunk_entities = emb.entities or []
        if not isinstance(chunk_entities, list):
            # Defensive: if a row got non-list JSON somehow, skip it
            # rather than crashing the retriever.
            continue
        overlap = len(query_set.intersection(chunk_entities))
        if overlap == 0:
            # On SQLite the prefilter doesn't run, so we may have rows
            # with zero overlap; drop them here.
            continue
        scored.append((emb, float(overlap)))

    # Score desc; tiebreak preserved from SQL order_by (created_at desc,
    # id desc) because Python sort is stable and we already iterate in
    # that order.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


def _bm25_prefilter_tokens(query: str) -> list[str]:
    """Unique, lowercase word tokens used for the bm25_search_chunks prefilter.

    .lower() (not .casefold()) matches the SQL func.lower() applied to the
    column and the BM25 scorer's tokenization, keeping prefilter and scoring
    in lockstep.
    """
    raw_tokens = re.findall(r"\w+", query.lower(), flags=re.UNICODE)
    unique_tokens = list(dict.fromkeys(raw_tokens))
    return unique_tokens[:BM25_PREFILTER_MAX_QUERY_TOKENS]


def _escape_like(token: str) -> str:
    """Escape SQL LIKE wildcards so query tokens match literally."""
    return (
        token.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


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
        # Single unique match: award 1.0 — it is the top result by definition.
        # Multiple docs with identical scores: award 0.0 — the signal is
        # uninformative and must not inflate every doc's fusion contribution.
        flat_score = 1.0 if len(scored) == 1 else 0.0
        return [(emb, flat_score) for emb, _ in scored]
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


def _is_en_query(query: str, query_script_bucket: str) -> bool:
    """Return True when the query is safe for English BM25 (pure ASCII).

    Includes the "other" bucket (digits, punctuation, symbols) — these contain
    no non-ASCII characters so BM25 against an English corpus is always safe.
    """
    if query_script_bucket not in ("latin", "other"):
        return False
    return all(ord(c) < 128 for c in query)


def _bm25_queries_for_script(
    query: str,
    query_variants: list[str],
    query_script_bucket: str,
    *,
    kb_script: str | None = None,
) -> list[str]:
    """Select BM25 query list based on script bucket and KB language.

    English queries use the original text.  For non-EN queries the order depends
    on whether the KB is in the same script as the query:

    - Same script (e.g. Russian query, Russian KB): original first so that the
      asymmetric BM25 mode uses the native-language query for lexical matching.
    - Different script / unknown (e.g. Russian query, English KB): EN rewrite
      first so asymmetric mode uses the rewrite for lexical matching.

    Both variants are always included so symmetric mode evaluates both.
    """
    if _is_en_query(query, query_script_bucket):
        return [query]
    rewritten = next(
        (
            v
            for v in reversed(query_variants)
            if _is_en_query(v, detect_query_script_bucket(v))
        ),
        None,
    )
    if not rewritten:
        return [query]
    # Same-language KB: original first (asymmetric uses [0] = native query).
    if kb_script and kb_script == query_script_bucket:
        return [query, rewritten]
    # Cross-lingual KB or unknown: EN rewrite first (asymmetric uses [0] = EN).
    return [rewritten, query]


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
        # Use the first variant query (may be an EN rewrite for non-EN queries).
        effective_query = variant_queries[0] if variant_queries else query
        results = _score_prepared_bm25_corpus(prepared_corpus, effective_query, top_k)
        winner_by_id = {
            embedding.id: BM25Winner(variant_index=0, variant_query=effective_query, score=score)
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
    *,
    entity_results: list[tuple[Embedding, float]] | None = None,
) -> list[tuple[Embedding, float]]:
    """Combine vector + BM25 (+ optional entity-overlap) results using RRF.

    Each input list contributes 1/(k+rank+1) per position. The score for
    a given chunk is the sum of contributions across whichever channels
    surfaced it. ``entity_results`` is keyword-only because three
    same-typed positional ranked lists are easy to mix up at call sites;
    keeping it named makes the third-channel intent obvious. ``None``
    (the default) means "skip the entity channel entirely" — when
    ``settings.entity_overlap_enabled`` is off the caller passes None
    and we degrade to the two-channel formula with zero added cost.
    """
    scores: dict[uuid.UUID, float] = {}
    id_to_emb: dict[uuid.UUID, Embedding] = {}

    for rank, (emb, _) in enumerate(vector_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    for rank, (emb, _) in enumerate(bm25_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    if entity_results:
        for rank, (emb, _) in enumerate(entity_results):
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
    lexical_query: str | None = None,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Apply an interim heuristic reranking stage over fused candidates.

    lexical_query: when the user query is non-EN, pass the EN rewrite here so
    that _lexical_overlap_score operates against English corpus text instead of
    always returning ~0 for non-ASCII queries.
    """
    if not candidates:
        return []

    max_rrf = max(score for _, score in candidates)
    vector_scores = vector_scores or {}
    bm25_scores = bm25_scores or {}
    effective_lexical_query = lexical_query or query

    rescored: list[tuple[Embedding, float]] = []
    for embedding, rrf_score in candidates:
        lexical_score = _lexical_overlap_score(effective_lexical_query, embedding.chunk_text or "")
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
    tenant_id: uuid.UUID,
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
            .filter(Document.tenant_id == tenant_id)
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
        return _python_cosine_search(tenant_id, query_vector, top_k, db)


def search_similar_chunks(
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """Compatibility wrapper returning ranked results only."""
    return search_similar_chunks_detailed(
        tenant_id=tenant_id,
        query=query,
        top_k=top_k,
        db=db,
        api_key=api_key,
    ).results


# ---------------------------------------------------------------------------
# Pipeline stage dataclasses — private to search_similar_chunks_detailed
# ---------------------------------------------------------------------------


@dataclass
class _QueryStageResult:
    query_variants: list[str]
    variant_vectors: list[list[float]]
    query_variant_count: int
    variant_mode: VariantMode
    extra_variant_count: int
    embedded_query_count: int
    extra_embedded_queries: int
    embedding_api_request_count: int
    extra_embedding_api_requests: int
    query_embedding_duration_ms: float
    query_script_bucket: str
    rewritten_variant: str | None
    trace_query_vector: list[float]


@dataclass
class _CandidateStageResult:
    vector_candidates: list[tuple[Embedding, float]]
    vector_search_call_count: int
    vector_duration_ms: float
    vector_engine: str
    bm25_variant_queries: list[str]
    bm25_bundle: BM25SearchBundle
    bm25_duration_ms: float
    bm25_expansion_mode: BM25ExpansionMode
    fused_results: list[tuple[Embedding, float]]
    rrf_duration_ms: float
    best_vector_similarity: float | None
    best_keyword_score: float | None
    rerank_lexical_query: str | None


@dataclass
class _RankingStageResult:
    final_results: list[tuple[Embedding, float]]
    vector_similarities: list[float | None]
    mmr_selection: MMRSelectionResult


@dataclass
class _QualityStageResult:
    reliability: RetrievalReliability


# ---------------------------------------------------------------------------
# Stage functions
# ---------------------------------------------------------------------------


def _run_query_stage(
    *,
    query: str,
    api_key: str,
    trace: TraceHandle | None,
    precomputed_query_variants: list[str] | None,
    precomputed_variant_vectors: list[list[float]] | None,
    precomputed_embedding_api_request_count: int | None,
    precomputed_rewritten_variant: str | None,
    embedding_timeout: float | None,
) -> _QueryStageResult:
    use_precomputed = (
        precomputed_query_variants is not None
        and precomputed_variant_vectors is not None
        and precomputed_query_variants
        and len(precomputed_query_variants) == len(precomputed_variant_vectors)
    )

    query_variants = precomputed_query_variants if use_precomputed else expand_query(query)

    # Semantic query rewriting: add a documentation-style keyword variant of the
    # user's question (in the same language). Bridges the framing gap between
    # user problem-descriptions and feature-name headings in docs. Language-agnostic —
    # the multilingual embedding model handles cross-lingual matching from there.
    rewritten_variant: str | None = None
    if not use_precomputed:
        rewritten_variant = _rewrite_query_for_retrieval(query, api_key=api_key)
        if rewritten_variant:
            query_variants = _normalize_query_variants([*query_variants, rewritten_variant])

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
                # On the precomputed path (chat pipeline), rewritten_variant stays
                # None (the in-search rewrite is skipped); surface the value that
                # was computed upstream so traces reflect the actual rewrite used.
                "rewritten_variant": rewritten_variant or precomputed_rewritten_variant,
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
            timeout=embedding_timeout,
        )
        query_embedding_duration_ms = round((perf_counter() - embedding_started_at) * 1000, 2)
        embedded_query_count = len(query_variants)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []
        if trace is not None:
            trace.span(
                name="query-embedding",
                input={
                    "query_variants": query_variants,
                    "query_variant_count": query_variant_count,
                    "variant_mode": variant_mode,
                    "model": settings.embedding_model,
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

    return _QueryStageResult(
        query_variants=query_variants,
        variant_vectors=variant_vectors,
        query_variant_count=query_variant_count,
        variant_mode=variant_mode,
        extra_variant_count=extra_variant_count,
        embedded_query_count=embedded_query_count,
        extra_embedded_queries=extra_embedded_queries,
        embedding_api_request_count=embedding_api_request_count,
        extra_embedding_api_requests=extra_embedding_api_requests,
        query_embedding_duration_ms=query_embedding_duration_ms,
        query_script_bucket=detect_query_script_bucket(query),
        rewritten_variant=rewritten_variant,
        trace_query_vector=trace_query_vector,
    )


def _tenant_has_embeddings(tenant_id: uuid.UUID, db: Session) -> bool:
    """Cheap existence check: does the tenant have any indexed chunk?

    Used to gate the NER submission for the entity-overlap channel —
    tenants with zero embeddings will hit the empty-vector early-return
    downstream, so spending an OpenAI NER call for them is pure waste.

    Implemented as a ``LIMIT 1`` against the same ``embeddings`` table
    the vector search uses; with the existing ``ix_embeddings_document_id``
    index this is ~1-2ms even on multi-million-row deploys. Returns
    False on any DB error (defensive — a transient failure here must
    not block retrieval).
    """
    try:
        return (
            db.query(Embedding.id)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .limit(1)
            .first()
        ) is not None
    except Exception:
        logger.warning("tenant_has_embeddings_check_failed", exc_info=True)
        return False


def _cleanup_ner_executor(
    executor: ThreadPoolExecutor | None,
    future: Future[list[str]] | None,
) -> None:
    """Cancel a pending NER future and shut down its executor.

    Used both on the main path (after we've consumed the result) and
    on the early-return path (when vector search produced no candidates
    so the entity channel will not be invoked anyway). Best-effort —
    a failure here cannot leak into chat.
    """
    if future is not None:
        try:
            future.cancel()
        except Exception:
            pass
    if executor is not None:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            # Older Python without cancel_futures — fall back.
            executor.shutdown(wait=False)
        except Exception:
            pass


def _run_candidate_stage(
    *,
    tenant_id: uuid.UUID,
    query: str,
    query_stage: _QueryStageResult,
    top_k: int,
    db: Session,
    trace: TraceHandle | None,
    api_key: str | None = None,
) -> _CandidateStageResult:
    q = query_stage
    db_url = str(db.bind.url if db.bind else "")
    vector_engine = "python-cosine" if "sqlite" in db_url else "pgvector"
    vector_search_fn = _python_cosine_search if "sqlite" in db_url else _pgvector_search
    bm25_expansion_mode = _resolve_bm25_expansion_mode()

    kb_script = detect_tenant_kb_script(tenant_id, db)
    bm25_variant_queries = _bm25_queries_for_script(
        query, q.query_variants, q.query_script_bucket, kb_script=kb_script
    )
    # EN rewrite to use for lexical scoring in reranker when query is non-EN.
    rerank_lexical_query: str | None = (
        None
        if _is_en_query(query, q.query_script_bucket)
        else (bm25_variant_queries[0] if bm25_variant_queries else None)
    )

    # ── Step 5+: kick off NER for the entity-overlap channel concurrently
    # with vector + BM25 retrieval. Sequential execution would add the full
    # NER latency (~1-2s) to every chat turn — multi-turn cases multiplied
    # this into ~+8s p50 in the eval (see ClickUp 86exe5pjx). Parallel
    # execution overlaps NER with the existing vector + BM25 budget so the
    # channel's added latency drops to ~max(0, ner_ms - retrieval_ms),
    # which on prod traffic is typically zero (retrieval is the slower
    # branch). NER's internal wall-clock timeout still bounds the work,
    # so a slow OpenAI call cannot stall this hot path.
    #
    # Gating: only submit NER when the tenant has any indexed embeddings.
    # Otherwise vector_candidates will be empty downstream, the entity
    # channel won't run, and a submitted NER would just waste an OpenAI
    # call (the future cancel cannot kill an already-running thread —
    # ``cancel_futures=True`` in shutdown only stops queued ones).
    # The pre-check is one ``LIMIT 1`` against the same table the vector
    # search hits, ~1-2ms with the document_id index. Cheap insurance
    # against paying for NER on freshly-onboarded / empty-FAQ tenants.
    ner_executor: ThreadPoolExecutor | None = None
    ner_future: Future[list[str]] | None = None
    if settings.entity_overlap_enabled and api_key and _tenant_has_embeddings(
        tenant_id, db
    ):
        ner_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="entity_overlap_ner",
        )
        ner_future = ner_executor.submit(
            extract_entities_from_query,
            query,
            api_key,
            tenant_id=str(tenant_id),
        )

    # Build one shared candidate set before lexical stages: engine-specific
    # acquisition, then cross-variant merge/dedup/truncation.
    vector_candidate_set = _build_vector_candidate_set(
        tenant_id,
        q.variant_vectors,
        db,
        vector_search_fn=vector_search_fn,
    )
    vector_candidates = vector_candidate_set.candidates
    vector_search_call_count = vector_candidate_set.call_count
    vector_duration_ms = vector_candidate_set.duration_ms
    extra_vector_search_calls = max(vector_search_call_count - 1, 0)

    if not vector_candidates:
        # Empty corpus or no vector matches — entity channel won't run
        # downstream, so cancel the pending NER call to free its thread
        # and avoid wasting an OpenAI request on an unused result.
        _cleanup_ner_executor(ner_executor, ner_future)
        if trace is not None:
            trace.span(
                name="vector-search",
                input={
                    "query_embedding": format_query_embedding_preview(q.trace_query_vector),
                    "query_variants": q.query_variants,
                    "tenant_id": str(tenant_id),
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
        return _CandidateStageResult(
            vector_candidates=[],
            vector_search_call_count=vector_search_call_count,
            vector_duration_ms=vector_duration_ms,
            vector_engine=vector_engine,
            bm25_variant_queries=bm25_variant_queries,
            bm25_bundle=BM25SearchBundle(
                results=[],
                has_lexical_signal=False,
                variant_queries=bm25_variant_queries or [query],
                variant_eval_count=0,
                merged_hit_count_before_cap=0,
                merged_hit_count_after_cap=0,
                winner_by_id={},
            ),
            bm25_duration_ms=0.0,
            bm25_expansion_mode=bm25_expansion_mode,
            fused_results=[],
            rrf_duration_ms=0.0,
            best_vector_similarity=None,
            best_keyword_score=None,
            rerank_lexical_query=rerank_lexical_query,
        )

    vector_embs = [emb for emb, _ in vector_candidates]
    if trace is not None:
        trace.span(
            name="vector-search",
            input={
                "query_embedding": format_query_embedding_preview(q.trace_query_vector),
                "query_variants": q.query_variants,
                "tenant_id": str(tenant_id),
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
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={
                "query": query,
                "query_variants": bm25_bundle.variant_queries,
                "tenant_id": str(tenant_id),
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
                    bm25_bundle.results,
                    winner_by_id=bm25_bundle.winner_by_id,
                ),
                "duration_ms": bm25_duration_ms,
                "bm25_query_variant_count": len(bm25_bundle.variant_queries),
                "bm25_variant_eval_count": bm25_bundle.variant_eval_count,
                "extra_bm25_variant_evals": max(bm25_bundle.variant_eval_count - 1, 0),
                "bm25_merged_hit_count_before_cap": bm25_bundle.merged_hit_count_before_cap,
                "bm25_merged_hit_count_after_cap": bm25_bundle.merged_hit_count_after_cap,
            }
        )

    vector_for_rrf = vector_candidates[:rrf_candidate_pool]

    # ── Step 5+: harvest the parallel NER future and run entity overlap.
    # The NER call was kicked off at the start of this stage (concurrent
    # with vector + BM25). Here we just wait for whatever's left of the
    # NER work and run the cheap DB lookup for entity overlap.
    #
    # ``entity_duration_ms`` measures the wait-and-lookup time the
    # caller actually paid on top of vector + BM25. With healthy NER
    # latency it's near-zero (NER usually finished while retrieval was
    # running). The total NER work-time itself is observable via
    # Langfuse trace + the timing of the future result — callers don't
    # need it to make rollout decisions.
    entity_results: list[tuple[Embedding, float]] = []
    query_entities: list[str] = []
    entity_duration_ms = 0.0
    if ner_future is not None and ner_executor is not None:
        wait_started_at = perf_counter()
        try:
            # extract_entities_from_query has its own ~2s wall-clock
            # timeout + empty-list fallback inside _run_with_timeout.
            # The small margin here is just to let the inner thread
            # actually return in case the timeout fired right at the
            # wire — ``cancel_futures=True`` in cleanup handles the
            # rare case where the inner thread is still draining.
            query_entities = ner_future.result(
                timeout=settings.ner_query_timeout_seconds + 0.5
            )
        except Exception:
            logger.warning("ner_future_result_failed", exc_info=True)
            query_entities = []
        finally:
            _cleanup_ner_executor(ner_executor, ner_future)
        if query_entities:
            entity_results = entity_overlap_search(
                tenant_id=tenant_id,
                query_entities=query_entities,
                top_k=rrf_candidate_pool,
                db=db,
            )
        entity_duration_ms = round((perf_counter() - wait_started_at) * 1000, 2)
        if trace is not None:
            trace.span(
                name="entity-overlap-search",
                input={
                    "query": query,
                    "tenant_id": str(tenant_id),
                    "top_k": rrf_candidate_pool,
                    "query_entities": query_entities,
                },
            ).end(
                output={
                    "chunks": format_embedding_results(
                        entity_results,
                        score_name="entity_overlap_score",
                    ),
                    "duration_ms": entity_duration_ms,
                    "query_entity_count": len(query_entities),
                    "candidate_count": len(entity_results),
                }
            )

        # PostHog: per-chat-turn record of how the entity channel
        # actually behaved. Lets us answer at-scale questions like
        # "what fraction of queries surface ≥1 entity?", "what's the
        # p95 NER+lookup latency?", "how often does the channel return
        # zero candidates?". One event per retrieval; aggregated by
        # tenant in the dashboard. Best-effort: failure to emit must
        # never break the chat hot path.
        try:
            capture_event(
                "entity_overlap.channel_used",
                distinct_id=str(tenant_id) if tenant_id else "system",
                tenant_id=str(tenant_id) if tenant_id else None,
                properties={
                    "channel": "entity_overlap",
                    "query_entity_count": len(query_entities),
                    "had_query_entities": bool(query_entities),
                    "candidate_count": len(entity_results),
                    "duration_ms": entity_duration_ms,
                },
                groups={"tenant": str(tenant_id)} if tenant_id else None,
            )
        except Exception:
            logger.warning("Failed to emit entity_overlap.channel_used", exc_info=True)

    rrf_started_at = perf_counter()
    fused_results = reciprocal_rank_fusion(
        vector_for_rrf,
        bm25_bundle.results,
        top_k=rrf_candidate_pool,
        entity_results=entity_results or None,
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
                    bm25_bundle.results,
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

    return _CandidateStageResult(
        vector_candidates=vector_candidates,
        vector_search_call_count=vector_search_call_count,
        vector_duration_ms=vector_duration_ms,
        vector_engine=vector_engine,
        bm25_variant_queries=bm25_variant_queries,
        bm25_bundle=bm25_bundle,
        bm25_duration_ms=bm25_duration_ms,
        bm25_expansion_mode=bm25_expansion_mode,
        fused_results=fused_results,
        rrf_duration_ms=rrf_duration_ms,
        best_vector_similarity=vector_candidates[0][1] if vector_candidates else None,
        best_keyword_score=bm25_bundle.results[0][1] if bm25_bundle.results else None,
        rerank_lexical_query=rerank_lexical_query,
    )


def _run_ranking_stage(
    *,
    query: str,
    query_stage: _QueryStageResult,
    candidate_stage: _CandidateStageResult,
    top_k: int,
    trace: TraceHandle | None,
) -> _RankingStageResult:
    q = query_stage
    c = candidate_stage

    rerank_started_at = perf_counter()
    reranked_results = rerank_candidates(
        query,
        c.fused_results,
        vector_scores=_collect_score_map(c.vector_candidates),
        bm25_scores=_collect_score_map(c.bm25_bundle.results),
        lexical_query=c.rerank_lexical_query,
        top_k=top_k,
    )
    if trace is not None:
        trace.span(
            name="reranking",
            input={
                "query": query,
                "candidate_count": len(c.fused_results),
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
        q.query_script_bucket,
        reranked_results,
        top_k=top_k * 2,
    )
    if trace is not None:
        trace.span(
            name="script-boost",
            input={
                "query_script_bucket": q.query_script_bucket,
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
    mmr_selection = mmr_select(script_boosted_results, top_k=top_k)
    final_results = mmr_selection.results
    vector_similarity_by_id = {emb.id: sim for emb, sim in c.vector_candidates}
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

    return _RankingStageResult(
        final_results=final_results,
        vector_similarities=vector_similarities,
        mmr_selection=mmr_selection,
    )


def _run_quality_stage(
    *,
    final_results: list[tuple[Embedding, float]],
    tenant_id: uuid.UUID,
    db: Session,
    api_key: str,
    trace: TraceHandle | None,
) -> _QualityStageResult:
    overlap_started_at = perf_counter()
    source_overlap_detected, source_overlap_pairs = detect_source_overlaps(final_results)
    contradiction_pairs = detect_metadata_contradictions(final_results, source_overlap_pairs)
    client_row: Tenant | None = None
    if settings.contradiction_adjudication_enabled and hasattr(db, "query"):
        client_row = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    contradiction_adjudication, contradiction_adjudication_observability = (
        _build_contradiction_adjudication_evidence(
            contradiction_pairs=contradiction_pairs,
            final_results=final_results,
            tenant=client_row,
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
    return _QualityStageResult(reliability=reliability)


def _build_empty_result_bundle(
    q: _QueryStageResult,
    c: _CandidateStageResult,
    retrieval_duration_ms: float,
) -> SearchResultBundle:
    return SearchResultBundle(
        results=[],
        query_variants=q.query_variants,
        query_script_bucket=q.query_script_bucket,
        reliability=build_reliability_assessment(top_score=None, result_count=0),
        query_variant_count=q.query_variant_count,
        variant_mode=q.variant_mode,
        extra_variant_count=q.extra_variant_count,
        embedded_query_count=q.embedded_query_count,
        extra_embedded_queries=q.extra_embedded_queries,
        embedding_api_request_count=q.embedding_api_request_count,
        extra_embedding_api_requests=q.extra_embedding_api_requests,
        vector_search_call_count=c.vector_search_call_count,
        extra_vector_search_calls=max(c.vector_search_call_count - 1, 0),
        bm25_expansion_mode=c.bm25_expansion_mode,
        bm25_query_variant_count=len(c.bm25_variant_queries),
        bm25_variant_eval_count=0,
        extra_bm25_variant_evals=0,
        retrieval_duration_ms=retrieval_duration_ms,
        query_embedding_duration_ms=q.query_embedding_duration_ms,
        vector_search_duration_ms=c.vector_duration_ms,
    )


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


def search_similar_chunks_detailed(
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
    trace: TraceHandle | None = None,
    precomputed_query_variants: list[str] | None = None,
    precomputed_variant_vectors: list[list[float]] | None = None,
    precomputed_embedding_api_request_count: int | None = None,
    precomputed_rewritten_variant: str | None = None,
    embedding_timeout: float | None = None,
) -> SearchResultBundle:
    """
    Hybrid search: pgvector cosine similarity + BM25, merged with RRF.

    PostgreSQL uses pgvector for candidate acquisition, while SQLite uses
    Python cosine search. Downstream ranking and observability stages are shared.
    """
    retrieval_started_at = perf_counter()

    if embedding_timeout is None:
        embedding_timeout = settings.embedding_http_timeout_seconds

    q = _run_query_stage(
        query=query,
        api_key=api_key,
        trace=trace,
        precomputed_query_variants=precomputed_query_variants,
        precomputed_variant_vectors=precomputed_variant_vectors,
        precomputed_embedding_api_request_count=precomputed_embedding_api_request_count,
        precomputed_rewritten_variant=precomputed_rewritten_variant,
        embedding_timeout=embedding_timeout,
    )
    c = _run_candidate_stage(
        tenant_id=tenant_id,
        query=query,
        query_stage=q,
        top_k=top_k,
        db=db,
        trace=trace,
        api_key=api_key,
    )
    if not c.vector_candidates:
        return _build_empty_result_bundle(
            q, c, round((perf_counter() - retrieval_started_at) * 1000, 2)
        )

    r = _run_ranking_stage(
        query=query,
        query_stage=q,
        candidate_stage=c,
        top_k=top_k,
        trace=trace,
    )
    quality = _run_quality_stage(
        final_results=r.final_results,
        tenant_id=tenant_id,
        db=db,
        api_key=api_key,
        trace=trace,
    )
    return SearchResultBundle(
        results=r.final_results,
        best_vector_similarity=c.best_vector_similarity,
        vector_similarities=r.vector_similarities,
        best_keyword_score=c.best_keyword_score,
        has_lexical_signal=c.bm25_bundle.has_lexical_signal,
        query_variants=q.query_variants,
        query_script_bucket=q.query_script_bucket,
        reliability=quality.reliability,
        query_variant_count=q.query_variant_count,
        variant_mode=q.variant_mode,
        extra_variant_count=q.extra_variant_count,
        embedded_query_count=q.embedded_query_count,
        extra_embedded_queries=q.extra_embedded_queries,
        embedding_api_request_count=q.embedding_api_request_count,
        extra_embedding_api_requests=q.extra_embedding_api_requests,
        vector_search_call_count=c.vector_search_call_count,
        extra_vector_search_calls=max(c.vector_search_call_count - 1, 0),
        bm25_expansion_mode=c.bm25_expansion_mode,
        bm25_query_variant_count=len(c.bm25_bundle.variant_queries),
        bm25_variant_eval_count=c.bm25_bundle.variant_eval_count,
        extra_bm25_variant_evals=max(c.bm25_bundle.variant_eval_count - 1, 0),
        bm25_merged_hit_count_before_cap=c.bm25_bundle.merged_hit_count_before_cap,
        bm25_merged_hit_count_after_cap=c.bm25_bundle.merged_hit_count_after_cap,
        retrieval_duration_ms=round((perf_counter() - retrieval_started_at) * 1000, 2),
        query_embedding_duration_ms=q.query_embedding_duration_ms,
        vector_search_duration_ms=c.vector_duration_ms,
    )


def _python_cosine_search(
    tenant_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    Fallback: Python-based cosine similarity search.

    Used for SQLite (tests) or when pgvector is not available.
    Not recommended for production with large datasets.

    Args:
        tenant_id: Tenant ID for filtering.
        query_vector: Pre-computed query embedding.
        top_k: Number of results.
        db: Database session.
    """

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.tenant_id == tenant_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        if emb.vector is not None:
            vector: list[float] | None = list(emb.vector)
            meta_vec = (emb.metadata_json or {}).get("vector")
            if meta_vec is not None and meta_vec != vector:
                logger.warning(
                    "embedding %s: emb.vector diverges from metadata_json[vector]",
                    emb.id,
                )
        else:
            meta = emb.metadata_json or {}
            vector = meta.get("vector")

        if not vector or not isinstance(vector, list) or len(vector) != len(query_vector):
            continue

        scored.append((emb, cosine_similarity(query_vector, vector)))

    return _sort_scored_embeddings(scored)[:top_k]


# ---------------------------------------------------------------------------
# Async layer — Phase 3 async migration (PR 1: search/service.py)
#
# All public async entry points are named with an ``_async`` suffix so sync
# callers (chat/guards, migrating in PR 2-3) continue to work unchanged.
# The async pipeline stages are private (``_async_*`` prefix).
# ---------------------------------------------------------------------------


def _session_is_sqlite(db: Session | AsyncSession) -> bool:
    """Detect SQLite from a sync or async session (for test/pg branching).

    Uses ``isinstance`` to pick the right bind accessor, then reads the
    backing engine's URL.
    """
    try:
        if isinstance(db, AsyncSession):
            bind = db.sync_session.bind
        else:
            bind = db.bind
        return "sqlite" in str(getattr(bind, "url", ""))
    except Exception:
        return False


# ── Async OpenAI helpers ─────────────────────────────────────────────────────


async def _async_rewrite_query_for_retrieval(query: str, *, api_key: str) -> str | None:
    """Async counterpart of :func:`_rewrite_query_for_retrieval`."""
    try:
        client = get_async_openai_client(api_key, timeout=QUERY_REWRITE_HTTP_TIMEOUT_SECONDS)
        response = await async_call_openai_with_retry(
            "query_rewrite_for_retrieval",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a search query optimizer for a product knowledge base.\n"
                            "Rewrite the user's question as 3-5 English keywords or a short English noun phrase "
                            "that would appear as a topic or heading in product documentation.\n"
                            "Always output in English regardless of the input language.\n"
                            "Output only the rewritten query, nothing else."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_completion_tokens=60,
            ),
        )
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten if rewritten else None
    except Exception:
        return None


async def async_embed_query(
    query: str,
    *,
    api_key: str,
    timeout: float | None = None,
    max_attempts: int | None = None,
) -> list[float]:
    """Async counterpart of :func:`embed_query`."""
    cached = _emb_cache.get(query)
    if cached is not None:
        return cached
    client = get_async_openai_client(api_key, timeout=timeout)
    response = await async_call_openai_with_retry(
        "search_embed_query",
        lambda: client.embeddings.create(
            model=settings.embedding_model,
            input=query,
        ),
        call_type="embedding",
        max_attempts=max_attempts,
    )
    vector = response.data[0].embedding
    _emb_cache.set(query, vector)
    return vector


async def async_embed_queries(
    queries: list[str],
    *,
    api_key: str,
    timeout: float | None = None,
) -> list[list[float]]:
    """Async counterpart of :func:`embed_queries`.

    Vectors for texts already in the in-process cache are returned without
    an API call; only cache misses are sent to OpenAI as a single batch.
    """
    if not queries:
        return []
    cached_map: dict[str, list[float] | None] = {q: _emb_cache.get(q) for q in queries}
    misses = [q for q in queries if cached_map[q] is None]
    if not misses:
        return [cached_map[q] for q in queries]  # type: ignore[return-value]
    client = get_async_openai_client(api_key, timeout=timeout)
    response = await async_call_openai_with_retry(
        "search_embed_queries",
        lambda: client.embeddings.create(
            model=settings.embedding_model,
            input=misses,
        ),
        call_type="embedding",
    )
    for text, item in zip(misses, response.data, strict=False):
        _emb_cache.set(text, item.embedding)
        cached_map[text] = item.embedding
    return [cached_map[q] for q in queries]  # type: ignore[return-value]


async def async_embed_queries_with_stats(
    queries: list[str], *, api_key: str, timeout: float | None = None
) -> tuple[list[list[float]], int]:
    """Async counterpart of :func:`embed_queries_with_stats`."""
    if not queries:
        return [], 0
    any_miss = any(_emb_cache.get(q) is None for q in queries)
    vectors = await async_embed_queries(queries, api_key=api_key, timeout=timeout)
    return vectors, (1 if any_miss else 0)


async def async_semantic_query_rewrite(
    query: str,
    *,
    api_key: str,
    timeout: float = 2.0,
    bot_id: str | None = None,
) -> str | None:
    """Async counterpart of :func:`semantic_query_rewrite`."""
    if not query or not api_key:
        return None
    prompt = _SEMANTIC_REWRITE_PROMPT_PREFIX + query + _SEMANTIC_REWRITE_PROMPT_SUFFIX
    try:
        client = get_async_openai_client(api_key, timeout=timeout)
        response = await async_call_openai_with_retry(
            "semantic_query_rewrite",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=_SEMANTIC_REWRITE_MAX_TOKENS,
            ),
            bot_id=bot_id,
        )
        rewrite = (response.choices[0].message.content or "").strip()
        if rewrite and "\n" not in rewrite and len(rewrite) <= 200:
            return rewrite
    except Exception:
        pass
    return None


async def async_semantic_query_rewrite_for_kb(
    query: str,
    *,
    kb_script: str,
    api_key: str,
    timeout: float = 2.0,
    bot_id: str | None = None,
) -> str | None:
    """Async counterpart of :func:`semantic_query_rewrite_for_kb`."""
    lang_name = _SCRIPT_TO_LANGUAGE_NAME.get(kb_script)
    if not lang_name or not query or not api_key:
        return None
    prompt = (
        _SEMANTIC_REWRITE_PROMPT_PREFIX
        + query
        + "\"\n\n"
        "Write a short technical search query (5-10 words) using product feature "
        "terminology and technical concepts that would retrieve the relevant "
        f"documentation. Focus on the FEATURE or SETTING being asked about, not "
        f"the user's symptom. Output ONLY in {lang_name}, regardless of the input "
        "language. Reply with ONLY the search query, nothing else."
    )
    try:
        client = get_async_openai_client(api_key, timeout=timeout)
        response = await async_call_openai_with_retry(
            "semantic_query_rewrite_for_kb",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=_SEMANTIC_REWRITE_MAX_TOKENS,
            ),
            bot_id=bot_id,
        )
        rewrite = (response.choices[0].message.content or "").strip()
        if rewrite and "\n" not in rewrite and len(rewrite) <= 200:
            return rewrite
    except Exception:
        pass
    return None


# ── Async DB helpers ─────────────────────────────────────────────────────────


async def _async_pgvector_search(
    tenant_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: AsyncSession,
) -> list[tuple[Embedding, float]]:
    """Async counterpart of :func:`_pgvector_search`."""
    try:
        distance_expr = Embedding.vector.cosine_distance(query_vector)
        stmt = (
            select(Embedding, distance_expr.label("distance"))
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(Embedding.vector.isnot(None))
            .order_by(distance_expr)
            .limit(top_k)
            .options(selectinload(Embedding.document))
        )
        result = await db.execute(stmt)
        rows = result.all()
        return [(row[0], max(0.0, 1.0 - row[1])) for row in rows]
    except Exception:
        logger.exception("async pgvector search failed; falling back to Python cosine search")
        return await _async_python_cosine_search(tenant_id, query_vector, top_k, db)


async def _async_python_cosine_search(
    tenant_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: AsyncSession,
) -> list[tuple[Embedding, float]]:
    """Async counterpart of :func:`_python_cosine_search`."""
    stmt = (
        select(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.tenant_id == tenant_id)
        .options(selectinload(Embedding.document))
    )
    result = await db.execute(stmt)
    embeddings = result.scalars().all()

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        if emb.vector is not None:
            vector: list[float] | None = list(emb.vector)
            meta_vec = (emb.metadata_json or {}).get("vector")
            if meta_vec is not None and meta_vec != vector:
                logger.warning(
                    "embedding %s: emb.vector diverges from metadata_json[vector]",
                    emb.id,
                )
        else:
            meta = emb.metadata_json or {}
            vector = meta.get("vector")

        if not vector or not isinstance(vector, list) or len(vector) != len(query_vector):
            continue
        scored.append((emb, cosine_similarity(query_vector, vector)))

    return _sort_scored_embeddings(scored)[:top_k]


async def _async_build_vector_candidate_set(
    tenant_id: uuid.UUID,
    variant_vectors: list[list[float]],
    db: AsyncSession,
    *,
    is_sqlite: bool = False,
) -> VectorCandidateSet:
    """Async counterpart of :func:`_build_vector_candidate_set`."""
    vector_started_at = perf_counter()
    vector_search_fn = _async_python_cosine_search if is_sqlite else _async_pgvector_search
    vector_candidate_map: dict[uuid.UUID, tuple[Embedding, float]] = {}
    vector_search_call_count = 0
    for variant_vector in variant_vectors:
        vector_search_call_count += 1
        for embedding, similarity in await vector_search_fn(
            tenant_id,
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


async def async_bm25_search_chunks(
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: AsyncSession,
) -> list[tuple[Embedding, float]]:
    """Async counterpart of :func:`bm25_search_chunks`."""
    tokens = _bm25_prefilter_tokens(query)
    if not tokens:
        return []

    token_conditions = [
        func.lower(Embedding.chunk_text).like(
            f"%{_escape_like(token)}%", escape="\\"
        )
        for token in tokens
    ]
    stmt = (
        select(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.tenant_id == tenant_id)
        .filter(Embedding.chunk_text.isnot(None))
        .filter(or_(*token_conditions))
        .order_by(Embedding.created_at.desc(), Embedding.id.desc())
        .limit(BM25_PREFILTER_CANDIDATE_LIMIT)
        .options(selectinload(Embedding.document))
    )
    result = await db.execute(stmt)
    embeddings = result.scalars().all()
    return _bm25_score_candidates(list(embeddings), query, top_k)


async def async_entity_overlap_search(
    tenant_id: uuid.UUID,
    query_entities: list[str],
    top_k: int,
    db: AsyncSession,
    *,
    is_sqlite: bool = False,
) -> list[tuple[Embedding, float]]:
    """Async counterpart of :func:`entity_overlap_search`."""
    if not query_entities or not tenant_id:
        return []

    if is_sqlite:
        stmt = (
            select(Embedding)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .options(selectinload(Embedding.document))
        )
    else:
        stmt = (
            select(Embedding)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(
                Embedding.entities.op("?|")(
                    cast(list(query_entities), ARRAY(SAText()))
                )
            )
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .limit(ENTITY_SEARCH_CANDIDATE_LIMIT)
            .options(selectinload(Embedding.document))
        )

    result = await db.execute(stmt)
    candidates = result.scalars().all()

    query_set = set(query_entities)
    scored: list[tuple[Embedding, float]] = []
    for emb in candidates:
        chunk_entities = emb.entities or []
        if not isinstance(chunk_entities, list):
            continue
        overlap = len(query_set.intersection(chunk_entities))
        if overlap == 0:
            continue
        scored.append((emb, float(overlap)))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


async def _async_tenant_has_embeddings(
    tenant_id: uuid.UUID, db: AsyncSession
) -> bool:
    """Async counterpart of :func:`_tenant_has_embeddings`."""
    try:
        stmt = (
            select(Embedding.id)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar() is not None
    except Exception:
        logger.warning("async_tenant_has_embeddings_check_failed", exc_info=True)
        return False


async def _async_kb_bucket_counts_from_languages(
    tenant_id: uuid.UUID, db: AsyncSession
) -> dict[str, int] | None:
    try:
        stmt = (
            select(Document.language)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.language.isnot(None))
        )
        result = await db.execute(stmt)
        rows = result.all()
    except Exception:
        return None
    if not rows:
        return None
    counts: dict[str, int] = {}
    for (lang,) in rows:
        bucket = _language_to_script_bucket(lang)
        if bucket is None:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


async def _async_kb_bucket_counts_from_chunk_sample(
    tenant_id: uuid.UUID, db: AsyncSession
) -> dict[str, int] | None:
    try:
        stmt = (
            select(Embedding.chunk_text)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .limit(_KB_SCRIPT_SAMPLE_SIZE)
        )
        result = await db.execute(stmt)
        sample = result.all()
    except Exception:
        return None
    if not sample:
        return None
    counts: dict[str, int] = {}
    for (chunk_text,) in sample:
        bucket = detect_query_script_bucket(chunk_text or "")
        if bucket in ("cyrillic", "latin"):
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


async def _async_tenant_has_unlabeled_documents(
    tenant_id: uuid.UUID, db: AsyncSession
) -> bool:
    try:
        stmt = (
            select(Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.language.is_(None))
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar() is not None
    except Exception:
        return False


async def _async_resolve_kb_bucket_counts(
    tenant_id: uuid.UUID, db: AsyncSession
) -> dict[str, int]:
    labeled = await _async_kb_bucket_counts_from_languages(tenant_id, db)
    if labeled is None:
        return await _async_kb_bucket_counts_from_chunk_sample(tenant_id, db) or {}
    if not await _async_tenant_has_unlabeled_documents(tenant_id, db):
        return labeled
    sampled = await _async_kb_bucket_counts_from_chunk_sample(tenant_id, db) or {}
    merged = dict(labeled)
    for bucket, count in sampled.items():
        merged[bucket] = merged.get(bucket, 0) + count
    return merged


async def async_detect_tenant_kb_script(
    tenant_id: uuid.UUID, db: AsyncSession
) -> str | None:
    """Async counterpart of :func:`detect_tenant_kb_script`."""
    key = str(tenant_id)
    now = time.monotonic()
    cached = _TENANT_KB_SCRIPT_CACHE.get(key)
    if cached is not None and now - cached[1] < _TENANT_KB_SCRIPT_CACHE_TTL:
        return cached[0]

    counts = await _async_resolve_kb_bucket_counts(tenant_id, db)
    result: str | None = None
    if counts:
        dominant = max(counts, key=counts.__getitem__)
        if dominant in ("cyrillic", "latin"):
            result = dominant

    _TENANT_KB_SCRIPT_CACHE[key] = (result, now)
    return result


async def async_detect_tenant_kb_scripts(
    tenant_id: uuid.UUID, db: AsyncSession
) -> frozenset[str]:
    """Async counterpart of :func:`detect_tenant_kb_scripts`."""
    key = str(tenant_id)
    now = time.monotonic()
    cached = _TENANT_KB_SCRIPTS_CACHE.get(key)
    if cached is not None and now - cached[1] < _TENANT_KB_SCRIPT_CACHE_TTL:
        return cached[0]

    counts = await _async_resolve_kb_bucket_counts(tenant_id, db)
    result = frozenset(b for b in counts if b in ("cyrillic", "latin"))
    _TENANT_KB_SCRIPTS_CACHE[key] = (result, now)
    return result


# ── Async pipeline stages ────────────────────────────────────────────────────


async def _async_run_query_stage(
    *,
    query: str,
    api_key: str,
    trace: TraceHandle | None,
    precomputed_query_variants: list[str] | None,
    precomputed_variant_vectors: list[list[float]] | None,
    precomputed_embedding_api_request_count: int | None,
    precomputed_rewritten_variant: str | None,
    embedding_timeout: float | None,
) -> _QueryStageResult:
    """Async counterpart of :func:`_run_query_stage`.

    Key optimization: when not using precomputed variants, the query-rewrite
    LLM call and the embedding of base variants are launched concurrently via
    ``asyncio.gather``, saving the latency of whichever finishes first.
    If the rewrite produces a new variant it is embedded in a second (fast)
    call afterward — total API calls stay the same as the sync path.
    """
    use_precomputed = (
        precomputed_query_variants is not None
        and precomputed_variant_vectors is not None
        and precomputed_query_variants
        and len(precomputed_query_variants) == len(precomputed_variant_vectors)
    )

    query_variants = precomputed_query_variants if use_precomputed else expand_query(query)

    rewritten_variant: str | None = None
    variant_vectors: list[list[float]] = []
    embedding_api_request_count = 0
    query_embedding_duration_ms = 0.0
    embedded_query_count = 0
    extra_embedded_queries = 0
    extra_embedding_api_requests = 0

    if not use_precomputed:
        embedding_started_at = perf_counter()
        # Parallel: LLM rewrite + base variant embedding
        rewritten_variant, (base_vectors, api_count) = await asyncio.gather(
            _async_rewrite_query_for_retrieval(query, api_key=api_key),
            async_embed_queries_with_stats(query_variants, api_key=api_key, timeout=embedding_timeout),
        )

        if rewritten_variant:
            normalized = _normalize_query_variants([*query_variants, rewritten_variant])
            new_variants = [v for v in normalized if v not in set(query_variants)]
            if new_variants:
                extra_vectors, extra_count = await async_embed_queries_with_stats(
                    new_variants, api_key=api_key, timeout=embedding_timeout
                )
                variant_vectors = base_vectors + extra_vectors
                embedding_api_request_count = api_count + extra_count
                query_variants = normalized
            else:
                variant_vectors = base_vectors
                embedding_api_request_count = api_count
        else:
            variant_vectors = base_vectors
            embedding_api_request_count = api_count

        query_embedding_duration_ms = round((perf_counter() - embedding_started_at) * 1000, 2)
        embedded_query_count = len(query_variants)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []

        if trace is not None:
            trace.span(
                name="query-expansion",
                input={"query": query},
            ).end(
                output={
                    "variants": query_variants,
                    "rewritten_variant": rewritten_variant,
                    "query_variant_count": len(query_variants),
                    "variant_mode": _variant_mode_for_count(len(query_variants)),
                    "extra_variant_count": max(len(query_variants) - 1, 0),
                }
            )
            trace.span(
                name="query-embedding",
                input={
                    "query_variants": query_variants,
                    "query_variant_count": len(query_variants),
                    "variant_mode": _variant_mode_for_count(len(query_variants)),
                    "model": settings.embedding_model,
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
    else:
        variant_vectors = precomputed_variant_vectors or []
        embedding_api_request_count = int(precomputed_embedding_api_request_count or 1)
        embedded_query_count = len(variant_vectors)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []
        if trace is not None:
            trace.span(
                name="query-expansion",
                input={"query": query},
            ).end(
                output={
                    "variants": query_variants,
                    "rewritten_variant": precomputed_rewritten_variant,
                    "query_variant_count": len(query_variants),
                    "variant_mode": _variant_mode_for_count(len(query_variants)),
                    "extra_variant_count": max(len(query_variants) - 1, 0),
                }
            )

    query_variant_count = len(query_variants)
    variant_mode = _variant_mode_for_count(query_variant_count)

    return _QueryStageResult(
        query_variants=query_variants,
        variant_vectors=variant_vectors,
        query_variant_count=query_variant_count,
        variant_mode=variant_mode,
        extra_variant_count=max(query_variant_count - 1, 0),
        embedded_query_count=embedded_query_count,
        extra_embedded_queries=extra_embedded_queries,
        embedding_api_request_count=embedding_api_request_count,
        extra_embedding_api_requests=extra_embedding_api_requests,
        query_embedding_duration_ms=query_embedding_duration_ms,
        query_script_bucket=detect_query_script_bucket(query),
        rewritten_variant=rewritten_variant,
        trace_query_vector=trace_query_vector,
    )


async def _async_run_candidate_stage(
    *,
    tenant_id: uuid.UUID,
    query: str,
    query_stage: _QueryStageResult,
    top_k: int,
    db: AsyncSession,
    trace: TraceHandle | None,
    api_key: str | None = None,
) -> _CandidateStageResult:
    """Async counterpart of :func:`_run_candidate_stage`.

    NER is run via ``run_in_executor`` so it remains concurrent with vector
    and BM25 retrieval without blocking the event loop.
    """
    q = query_stage
    is_sqlite = _session_is_sqlite(db)
    vector_engine = "python-cosine" if is_sqlite else "pgvector"
    bm25_expansion_mode = _resolve_bm25_expansion_mode()

    kb_script = await async_detect_tenant_kb_script(tenant_id, db)
    bm25_variant_queries = _bm25_queries_for_script(
        query, q.query_variants, q.query_script_bucket, kb_script=kb_script
    )
    rerank_lexical_query: str | None = (
        None
        if _is_en_query(query, q.query_script_bucket)
        else (bm25_variant_queries[0] if bm25_variant_queries else None)
    )

    # NER runs concurrently in the default executor (thread pool).
    ner_task: asyncio.Task[list[str]] | None = None
    loop = asyncio.get_running_loop()
    if settings.entity_overlap_enabled and api_key and await _async_tenant_has_embeddings(
        tenant_id, db
    ):
        ner_task = loop.run_in_executor(
            None,
            lambda: extract_entities_from_query(query, api_key, tenant_id=str(tenant_id)),
        )

    vector_candidate_set = await _async_build_vector_candidate_set(
        tenant_id,
        q.variant_vectors,
        db,
        is_sqlite=is_sqlite,
    )
    vector_candidates = vector_candidate_set.candidates
    vector_search_call_count = vector_candidate_set.call_count
    vector_duration_ms = vector_candidate_set.duration_ms
    extra_vector_search_calls = max(vector_search_call_count - 1, 0)

    if not vector_candidates:
        if ner_task is not None:
            ner_task.cancel()
        if trace is not None:
            trace.span(
                name="vector-search",
                input={
                    "query_embedding": format_query_embedding_preview(q.trace_query_vector),
                    "query_variants": q.query_variants,
                    "tenant_id": str(tenant_id),
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
        return _CandidateStageResult(
            vector_candidates=[],
            vector_search_call_count=vector_search_call_count,
            vector_duration_ms=vector_duration_ms,
            vector_engine=vector_engine,
            bm25_variant_queries=bm25_variant_queries,
            bm25_bundle=BM25SearchBundle(
                results=[],
                has_lexical_signal=False,
                variant_queries=bm25_variant_queries or [query],
                variant_eval_count=0,
                merged_hit_count_before_cap=0,
                merged_hit_count_after_cap=0,
                winner_by_id={},
            ),
            bm25_duration_ms=0.0,
            bm25_expansion_mode=bm25_expansion_mode,
            fused_results=[],
            rrf_duration_ms=0.0,
            best_vector_similarity=None,
            best_keyword_score=None,
            rerank_lexical_query=rerank_lexical_query,
        )

    vector_embs = [emb for emb, _ in vector_candidates]
    if trace is not None:
        trace.span(
            name="vector-search",
            input={
                "query_embedding": format_query_embedding_preview(q.trace_query_vector),
                "query_variants": q.query_variants,
                "tenant_id": str(tenant_id),
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
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={
                "query": query,
                "query_variants": bm25_bundle.variant_queries,
                "tenant_id": str(tenant_id),
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
                    bm25_bundle.results,
                    winner_by_id=bm25_bundle.winner_by_id,
                ),
                "duration_ms": bm25_duration_ms,
                "bm25_query_variant_count": len(bm25_bundle.variant_queries),
                "bm25_variant_eval_count": bm25_bundle.variant_eval_count,
                "extra_bm25_variant_evals": max(bm25_bundle.variant_eval_count - 1, 0),
                "bm25_merged_hit_count_before_cap": bm25_bundle.merged_hit_count_before_cap,
                "bm25_merged_hit_count_after_cap": bm25_bundle.merged_hit_count_after_cap,
            }
        )

    vector_for_rrf = vector_candidates[:rrf_candidate_pool]

    entity_results: list[tuple[Embedding, float]] = []
    query_entities: list[str] = []
    entity_duration_ms = 0.0
    if ner_task is not None:
        wait_started_at = perf_counter()
        try:
            query_entities = await asyncio.wait_for(
                asyncio.ensure_future(ner_task),
                timeout=settings.ner_query_timeout_seconds + 0.5,
            )
        except Exception:
            logger.warning("async_ner_task_failed", exc_info=True)
            query_entities = []
        if query_entities:
            entity_results = await async_entity_overlap_search(
                tenant_id=tenant_id,
                query_entities=query_entities,
                top_k=rrf_candidate_pool,
                db=db,
                is_sqlite=is_sqlite,
            )
        entity_duration_ms = round((perf_counter() - wait_started_at) * 1000, 2)
        if trace is not None:
            trace.span(
                name="entity-overlap-search",
                input={
                    "query": query,
                    "tenant_id": str(tenant_id),
                    "top_k": rrf_candidate_pool,
                    "query_entities": query_entities,
                },
            ).end(
                output={
                    "chunks": format_embedding_results(
                        entity_results,
                        score_name="entity_overlap_score",
                    ),
                    "duration_ms": entity_duration_ms,
                    "query_entity_count": len(query_entities),
                    "candidate_count": len(entity_results),
                }
            )
        try:
            capture_event(
                "entity_overlap.channel_used",
                distinct_id=str(tenant_id) if tenant_id else "system",
                tenant_id=str(tenant_id) if tenant_id else None,
                properties={
                    "channel": "entity_overlap",
                    "query_entity_count": len(query_entities),
                    "had_query_entities": bool(query_entities),
                    "candidate_count": len(entity_results),
                    "duration_ms": entity_duration_ms,
                },
                groups={"tenant": str(tenant_id)} if tenant_id else None,
            )
        except Exception:
            logger.warning("Failed to emit entity_overlap.channel_used", exc_info=True)

    rrf_started_at = perf_counter()
    fused_results = reciprocal_rank_fusion(
        vector_for_rrf,
        bm25_bundle.results,
        top_k=rrf_candidate_pool,
        entity_results=entity_results or None,
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
                    bm25_bundle.results,
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

    return _CandidateStageResult(
        vector_candidates=vector_candidates,
        vector_search_call_count=vector_search_call_count,
        vector_duration_ms=vector_duration_ms,
        vector_engine=vector_engine,
        bm25_variant_queries=bm25_variant_queries,
        bm25_bundle=bm25_bundle,
        bm25_duration_ms=bm25_duration_ms,
        bm25_expansion_mode=bm25_expansion_mode,
        fused_results=fused_results,
        rrf_duration_ms=rrf_duration_ms,
        best_vector_similarity=vector_candidates[0][1] if vector_candidates else None,
        best_keyword_score=bm25_bundle.results[0][1] if bm25_bundle.results else None,
        rerank_lexical_query=rerank_lexical_query,
    )


async def _async_run_quality_stage(
    *,
    final_results: list[tuple[Embedding, float]],
    tenant_id: uuid.UUID,
    db: AsyncSession,
    api_key: str,
    trace: TraceHandle | None,
) -> _QualityStageResult:
    """Async counterpart of :func:`_run_quality_stage`.

    ``adjudicate_contradictions`` is a sync LLM call; it runs in the default
    thread-pool executor so the event loop is not blocked.
    """
    overlap_started_at = perf_counter()
    source_overlap_detected, source_overlap_pairs = detect_source_overlaps(final_results)
    contradiction_pairs = detect_metadata_contradictions(final_results, source_overlap_pairs)

    client_row: Tenant | None = None
    if settings.contradiction_adjudication_enabled:
        stmt = select(Tenant).filter(Tenant.id == tenant_id)
        result = await db.execute(stmt)
        client_row = result.scalars().first()

    # adjudicate_contradictions is sync (LLM call) — run in executor to avoid
    # blocking the event loop. The helper itself is CPU-light; the OpenAI call
    # inside uses the sync client, which is fine in a thread context.
    loop = asyncio.get_running_loop()
    contradiction_adjudication, contradiction_adjudication_observability = await loop.run_in_executor(
        None,
        lambda: _build_contradiction_adjudication_evidence(
            contradiction_pairs=contradiction_pairs,
            final_results=final_results,
            tenant=client_row,
            api_key=api_key,
        ),
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
    return _QualityStageResult(reliability=reliability)


# ── Async public orchestrators ───────────────────────────────────────────────


async def search_similar_chunks_detailed_async(
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: AsyncSession,
    *,
    api_key: str,
    trace: TraceHandle | None = None,
    precomputed_query_variants: list[str] | None = None,
    precomputed_variant_vectors: list[list[float]] | None = None,
    precomputed_embedding_api_request_count: int | None = None,
    precomputed_rewritten_variant: str | None = None,
    embedding_timeout: float | None = None,
) -> SearchResultBundle:
    """Async counterpart of :func:`search_similar_chunks_detailed`.

    Runs the full hybrid retrieval pipeline on an ``AsyncSession``.
    The query-rewrite LLM call and embedding of base variants execute in
    parallel (``asyncio.gather``) for measurable latency savings on every turn.
    """
    retrieval_started_at = perf_counter()

    if embedding_timeout is None:
        embedding_timeout = settings.embedding_http_timeout_seconds

    q = await _async_run_query_stage(
        query=query,
        api_key=api_key,
        trace=trace,
        precomputed_query_variants=precomputed_query_variants,
        precomputed_variant_vectors=precomputed_variant_vectors,
        precomputed_embedding_api_request_count=precomputed_embedding_api_request_count,
        precomputed_rewritten_variant=precomputed_rewritten_variant,
        embedding_timeout=embedding_timeout,
    )
    c = await _async_run_candidate_stage(
        tenant_id=tenant_id,
        query=query,
        query_stage=q,
        top_k=top_k,
        db=db,
        trace=trace,
        api_key=api_key,
    )
    if not c.vector_candidates:
        return _build_empty_result_bundle(
            q, c, round((perf_counter() - retrieval_started_at) * 1000, 2)
        )

    r = _run_ranking_stage(
        query=query,
        query_stage=q,
        candidate_stage=c,
        top_k=top_k,
        trace=trace,
    )
    quality = await _async_run_quality_stage(
        final_results=r.final_results,
        tenant_id=tenant_id,
        db=db,
        api_key=api_key,
        trace=trace,
    )
    return SearchResultBundle(
        results=r.final_results,
        best_vector_similarity=c.best_vector_similarity,
        vector_similarities=r.vector_similarities,
        best_keyword_score=c.best_keyword_score,
        has_lexical_signal=c.bm25_bundle.has_lexical_signal,
        query_variants=q.query_variants,
        query_script_bucket=q.query_script_bucket,
        reliability=quality.reliability,
        query_variant_count=q.query_variant_count,
        variant_mode=q.variant_mode,
        extra_variant_count=q.extra_variant_count,
        embedded_query_count=q.embedded_query_count,
        extra_embedded_queries=q.extra_embedded_queries,
        embedding_api_request_count=q.embedding_api_request_count,
        extra_embedding_api_requests=q.extra_embedding_api_requests,
        vector_search_call_count=c.vector_search_call_count,
        extra_vector_search_calls=max(c.vector_search_call_count - 1, 0),
        bm25_expansion_mode=c.bm25_expansion_mode,
        bm25_query_variant_count=len(c.bm25_bundle.variant_queries),
        bm25_variant_eval_count=c.bm25_bundle.variant_eval_count,
        extra_bm25_variant_evals=max(c.bm25_bundle.variant_eval_count - 1, 0),
        bm25_merged_hit_count_before_cap=c.bm25_bundle.merged_hit_count_before_cap,
        bm25_merged_hit_count_after_cap=c.bm25_bundle.merged_hit_count_after_cap,
        retrieval_duration_ms=round((perf_counter() - retrieval_started_at) * 1000, 2),
        query_embedding_duration_ms=q.query_embedding_duration_ms,
        vector_search_duration_ms=c.vector_duration_ms,
    )


async def search_similar_chunks_async(
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: AsyncSession,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """Async counterpart of :func:`search_similar_chunks`."""
    return (
        await search_similar_chunks_detailed_async(
            tenant_id=tenant_id,
            query=query,
            top_k=top_k,
            db=db,
            api_key=api_key,
        )
    ).results
