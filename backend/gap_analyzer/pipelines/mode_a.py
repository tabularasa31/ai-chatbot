"""Mode A pipeline helpers: corpus sampling, chunk hashing, coverage scoring."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from backend.gap_analyzer._math import (
    _cosine_similarity,
    _tokenize,
    _vector_from_unknown,
    _vector_norm,
)
from backend.gap_analyzer.domain import DocumentScopePolicy
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.gap_analyzer.repository import (
    GapAnalyzerRepository,
    ModeACorpusChunk,
    ModeADismissalRecord,
)


@dataclass(frozen=True)
class _PreparedCorpusChunk:
    tokens: set[str]
    vector: list[float] | None
    vector_norm: float


@dataclass(frozen=True)
class _PreparedDismissal:
    normalized_label: str
    vector: list[float] | None
    vector_norm: float


def _batched(values: list[UUID], batch_size: int) -> Iterable[list[UUID]]:
    for index in range(0, len(values), batch_size):
        yield values[index : index + batch_size]


def _chunk_group_key(chunk: ModeACorpusChunk) -> str:
    for value in (
        chunk.section_title,
        chunk.page_title,
        chunk.filename,
        chunk.source_url,
    ):
        if value:
            return value.casefold()
    return f"{chunk.document_id}:{chunk.file_type}".casefold()


def _select_mode_a_sample(
    corpus_chunks: list[ModeACorpusChunk],
    *,
    max_chunks: int = 40,
) -> list[ModeACorpusChunk]:
    if not corpus_chunks:
        return []

    grouped: dict[str, list[ModeACorpusChunk]] = defaultdict(list)
    for chunk in corpus_chunks:
        grouped[_chunk_group_key(chunk)].append(chunk)

    selected: list[ModeACorpusChunk] = []
    selected_ids: set[UUID] = set()
    for group_key in sorted(grouped):
        best_in_group = sorted(
            grouped[group_key],
            key=lambda item: (-len(item.chunk_text), str(item.chunk_id)),
        )[0]
        selected.append(best_in_group)
        selected_ids.add(best_in_group.chunk_id)
        if len(selected) >= max_chunks:
            return selected

    remaining = [
        chunk
        for group_key in sorted(grouped)
        for chunk in grouped[group_key]
        if chunk.chunk_id not in selected_ids
    ]
    remaining.sort(
        key=lambda item: (
            -len(item.chunk_text),
            _chunk_group_key(item),
            str(item.chunk_id),
        )
    )
    for chunk in remaining:
        if len(selected) >= max_chunks:
            break
        selected.append(chunk)
    return selected


def _hash_sampled_chunks(chunks: list[ModeACorpusChunk]) -> str:
    joined = "|".join(sorted(str(chunk.chunk_id) for chunk in chunks))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _dedupe_candidates(candidates: list[ModeATopicCandidate]) -> list[ModeATopicCandidate]:
    deduped: list[ModeATopicCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.topic_label.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(candidate)
    return deduped


def _build_coverage_query(candidate: ModeATopicCandidate) -> str:
    parts = [candidate.topic_label.strip()]
    parts.extend(question.strip() for question in candidate.example_questions if question.strip())
    return "\n".join(parts)


def _bm25_coverage_score(score: float, match_kind: str) -> float:
    if match_kind == "exact_title":
        return 1.0
    if match_kind == "body":
        return 0.5 + (min(max(score, 0.0), 1.0) * 0.25)
    return 0.0


def _compute_gap_coverage(
    *,
    repository: GapAnalyzerRepository,
    tenant_id: UUID,
    query_text: str,
    query_embedding: list[float] | None,
) -> float:
    best_semantic = 0.0
    if query_embedding is not None:
        semantic_matches = repository.vector_top_k_for_tenant(
            tenant_id=tenant_id,
            query_embedding=query_embedding,
            top_k=3,
            excluded_file_types=DocumentScopePolicy().excluded_mode_a_file_types,
        )
        if semantic_matches:
            best_semantic = semantic_matches[0].score

    lexical_match = repository.bm25_match_for_tenant(
        tenant_id=tenant_id,
        query_text=query_text,
        excluded_file_types=DocumentScopePolicy().excluded_mode_a_file_types,
    )
    return max(best_semantic, _bm25_coverage_score(lexical_match.score, lexical_match.match_kind))


def _is_dismissed_candidate(
    *,
    candidate: ModeATopicCandidate,
    candidate_embedding: list[float] | None,
    candidate_embedding_norm: float,
    dismissals: list[_PreparedDismissal],
    similarity_threshold: float,
) -> bool:
    candidate_label = candidate.topic_label.strip().casefold()
    for dismissal in dismissals:
        if candidate_label == dismissal.normalized_label:
            return True
        if candidate_embedding is None or dismissal.vector is None:
            continue
        if _cosine_similarity(
            candidate_embedding,
            dismissal.vector,
            first_norm=candidate_embedding_norm,
            second_norm=dismissal.vector_norm,
        ) > similarity_threshold:
            return True
    return False


def _prepare_corpus_chunks(corpus_chunks: list[ModeACorpusChunk]) -> list[_PreparedCorpusChunk]:
    prepared: list[_PreparedCorpusChunk] = []
    for chunk in corpus_chunks:
        vector = _vector_from_unknown(chunk.vector)
        prepared.append(
            _PreparedCorpusChunk(
                tokens=set(_tokenize(chunk.chunk_text)),
                vector=vector,
                vector_norm=_vector_norm(vector),
            )
        )
    return prepared


def _prepare_dismissals(dismissals: list[ModeADismissalRecord]) -> list[_PreparedDismissal]:
    prepared: list[_PreparedDismissal] = []
    for dismissal in dismissals:
        vector = _vector_from_unknown(dismissal.topic_label_embedding)
        prepared.append(
            _PreparedDismissal(
                normalized_label=dismissal.topic_label.strip().casefold(),
                vector=vector,
                vector_norm=_vector_norm(vector),
            )
        )
    return prepared
