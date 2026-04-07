"""Public orchestrator for Gap Analyzer command flows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
from uuid import UUID

from backend.gap_analyzer.domain import CoveragePolicy, DocumentScopePolicy, GapLifecyclePolicy, SignalWeightPolicy
from backend.gap_analyzer.enums import GapCommandStatus
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.prompts import ModeATopicCandidate, embed_texts, extract_mode_a_candidates
from backend.gap_analyzer.repository import GapAnalyzerRepository, ModeACorpusChunk, ModeADismissalRecord
from backend.gap_analyzer.schemas import GapRunMode, ModeAResult, ModeBResult, RecalculateCommandResult

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


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


class GapAnalyzerOrchestrator:
    """Command routing and bounded Gap Analyzer behavior."""

    def __init__(self, repository: GapAnalyzerRepository | None = None) -> None:
        self.repository = repository

    def ingest_signal(self, signal: GapSignal) -> None:
        repository = self._require_repository()
        repository.store_signal(
            signal,
            signal_weight=self._resolve_signal_weight(signal),
        )

    def run_mode_a(self, tenant_id: UUID) -> ModeAResult:
        repository = self._require_repository()
        started_at = datetime.now(timezone.utc)

        corpus_chunks = repository.get_mode_a_corpus_chunks(
            tenant_id=tenant_id,
            excluded_file_types=DocumentScopePolicy().excluded_mode_a_file_types,
        )
        sampled_chunks = _select_mode_a_sample(corpus_chunks)
        extraction_chunk_hash = _hash_sampled_chunks(sampled_chunks)
        latest_hash = repository.get_latest_mode_a_hash(tenant_id)
        if latest_hash == extraction_chunk_hash:
            return ModeAResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        encrypted_api_key = repository.get_client_openai_key(tenant_id)
        if not encrypted_api_key:
            logger.warning("gap_analyzer_mode_a_missing_openai_key tenant_id=%s", tenant_id)
            return ModeAResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        raw_candidates = extract_mode_a_candidates(
            encrypted_api_key=encrypted_api_key,
            sampled_chunks=[chunk.chunk_text for chunk in sampled_chunks],
        )
        candidates = _dedupe_candidates(raw_candidates)
        label_embeddings, coverage_embeddings = self._embed_mode_a_candidates(
            encrypted_api_key=encrypted_api_key,
            candidates=candidates,
        )
        dismissals = repository.list_mode_a_dismissals(tenant_id)
        prepared_corpus = _prepare_corpus_chunks(corpus_chunks)
        prepared_dismissals = _prepare_dismissals(dismissals)

        coverage_policy = CoveragePolicy()
        lifecycle_policy = GapLifecyclePolicy()
        coverage_scores: dict[str, float] = {}
        topic_embeddings: dict[str, list[float]] = {}
        persisted_candidates: list[ModeATopicCandidate] = []

        for candidate in candidates:
            label_embedding = label_embeddings.get(candidate.topic_label)
            label_embedding_norm = _vector_norm(label_embedding)
            if _is_dismissed_candidate(
                candidate=candidate,
                candidate_embedding=label_embedding,
                candidate_embedding_norm=label_embedding_norm,
                dismissals=prepared_dismissals,
                similarity_threshold=lifecycle_policy.dismissal_similarity,
            ):
                continue

            coverage_query = _build_coverage_query(candidate)
            coverage_score = _compute_mode_a_coverage(
                query_text=coverage_query,
                query_embedding=coverage_embeddings.get(candidate.topic_label),
                corpus_chunks=prepared_corpus,
            )
            if coverage_score >= coverage_policy.mode_a_gate:
                continue

            coverage_scores[candidate.topic_label] = coverage_score
            if label_embedding is not None:
                topic_embeddings[candidate.topic_label] = label_embedding
            persisted_candidates.append(candidate)

        repository.replace_mode_a_topics(
            tenant_id=tenant_id,
            candidates=persisted_candidates,
            coverage_scores=coverage_scores,
            topic_embeddings=topic_embeddings,
            extraction_chunk_hash=extraction_chunk_hash,
        )
        return ModeAResult(
            tenant_id=tenant_id,
            status=GapCommandStatus.accepted,
            started_at=started_at,
        )

    async def run_mode_b(self, tenant_id: UUID) -> ModeBResult:
        raise NotImplementedError("Mode B pipeline lands in Phase 4")

    def record_assistant_feedback(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
        feedback_value: str,
    ) -> bool:
        if feedback_value not in {"up", "down", "none"}:
            return False

        repository = self._require_repository()
        signal_state = repository.get_signal_state_for_assistant_message(
            tenant_id=tenant_id,
            assistant_message_id=assistant_message_id,
        )
        if signal_state is None:
            return False

        repository.update_signal_weight(
            gap_question_id=signal_state.gap_question_id,
            signal_weight=self._resolve_signal_weight_from_values(
                answer_confidence=signal_state.answer_confidence,
                had_fallback=signal_state.had_fallback,
                was_rejected=signal_state.had_rejected,
                was_escalated=signal_state.had_escalation,
                user_thumbed_down=feedback_value == "down",
            ),
        )
        return True

    async def request_recalculation(
        self,
        tenant_id: UUID,
        mode: GapRunMode,
    ) -> RecalculateCommandResult:
        raise NotImplementedError("Async recalc orchestration lands in Phase 5")

    def _embed_mode_a_candidates(
        self,
        *,
        encrypted_api_key: str,
        candidates: list[ModeATopicCandidate],
    ) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        if not candidates:
            return {}, {}

        labels = [candidate.topic_label for candidate in candidates]
        coverage_queries = [_build_coverage_query(candidate) for candidate in candidates]
        label_vectors = embed_texts(encrypted_api_key=encrypted_api_key, texts=labels)
        coverage_vectors = embed_texts(encrypted_api_key=encrypted_api_key, texts=coverage_queries)
        return (
            {
                candidate.topic_label: label_vectors[index]
                for index, candidate in enumerate(candidates)
                if index < len(label_vectors)
            },
            {
                candidate.topic_label: coverage_vectors[index]
                for index, candidate in enumerate(candidates)
                if index < len(coverage_vectors)
            },
        )

    def _require_repository(self) -> GapAnalyzerRepository:
        if self.repository is None:
            raise RuntimeError("Gap Analyzer repository is required for command execution")
        return self.repository

    def _resolve_signal_weight(self, signal: GapSignal) -> float:
        return self._resolve_signal_weight_from_values(
            answer_confidence=signal.answer_confidence,
            had_fallback=signal.had_fallback,
            was_rejected=signal.was_rejected,
            was_escalated=signal.was_escalated,
            user_thumbed_down=signal.user_thumbed_down,
        )

    def _resolve_signal_weight_from_values(
        self,
        *,
        answer_confidence: float | None,
        had_fallback: bool,
        was_rejected: bool,
        was_escalated: bool,
        user_thumbed_down: bool,
    ) -> float:
        policy = SignalWeightPolicy()
        weight = policy.normal_weight
        if answer_confidence is not None and answer_confidence < policy.low_conf_threshold:
            weight = max(weight, policy.low_conf_weight)
        if was_rejected or had_fallback:
            weight = max(weight, policy.rejection_weight)
        if was_escalated:
            weight = max(weight, policy.escalation_weight)
        if user_thumbed_down:
            weight = max(weight, policy.thumbdown_weight)
        return weight


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


def _compute_mode_a_coverage(
    *,
    query_text: str,
    query_embedding: list[float] | None,
    corpus_chunks: list[_PreparedCorpusChunk],
) -> float:
    query_tokens = _tokenize(query_text)
    query_embedding_norm = _vector_norm(query_embedding)
    best_semantic = 0.0
    best_lexical = 0.0
    for chunk in corpus_chunks:
        if query_embedding is not None:
            best_semantic = max(
                best_semantic,
                _cosine_similarity(
                    query_embedding,
                    chunk.vector,
                    first_norm=query_embedding_norm,
                    second_norm=chunk.vector_norm,
                ),
            )
        if query_tokens:
            best_lexical = max(best_lexical, _token_overlap(query_tokens, chunk.tokens))
    return max(best_semantic, best_lexical)


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
                tokens=_tokenize(chunk.chunk_text),
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


def _vector_from_unknown(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [float(value) for value in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, list):
            return [float(value) for value in parsed]
    return None


def _vector_norm(vector: list[float] | None) -> float:
    if vector is None:
        return 0.0
    return math.sqrt(sum(value * value for value in vector))


def _cosine_similarity(
    first: list[float] | None,
    second: list[float] | None,
    *,
    first_norm: float,
    second_norm: float,
) -> float:
    if first is None or second is None or len(first) != len(second):
        return 0.0
    if first_norm == 0.0 or second_norm == 0.0:
        return 0.0
    dot = 0.0
    for left, right in zip(first, second):
        dot += left * right
    return max(0.0, min(1.0, dot / (first_norm * second_norm)))


def _token_overlap(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _tokenize(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.casefold()) if token}
