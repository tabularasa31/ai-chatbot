"""Reranking strategies over the fused retrieval candidate pool.

The heuristic reranker is the always-available baseline; the semantic ones
(LLM judge, local cross-encoder) re-score the wider fused pool by real
query/passage relevance and are selected per tenant. Every semantic strategy
runs under a hard wall-clock timeout and degrades to the heuristic on any
failure, so the chat turn never stalls or errors because of reranking.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import threading
import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client
from backend.core.openai_retry import async_call_openai_with_retry
from backend.models import Embedding, RerankerStrategy
from backend.utils.text import token_set

logger = logging.getLogger(__name__)

RERANK_LEXICAL_WEIGHT = 0.35
RERANK_VECTOR_WEIGHT = 0.25
RERANK_BM25_WEIGHT = 0.20
RERANK_RRF_WEIGHT = 0.20

# Semantic strategies blend their score with the heuristic one: the heuristic
# breaks ties on the coarse 0-10 LLM scale and keeps the hybrid-signal
# provenance in the final score consumed by the relevance gate.
SEMANTIC_SCORE_WEIGHT = 0.8
HEURISTIC_SCORE_WEIGHT = 0.2
RERANKER_MAX_CANDIDATES = 20
RERANKER_PASSAGE_CHARS = 600
LLM_SCORE_SCALE = 10.0
HEURISTIC_MODEL_NAME = "heuristic-rrf-v0"

ScoredEmbedding = tuple[Embedding, float]


@dataclass(frozen=True)
class RerankSignals:
    """Per-candidate retrieval signals the heuristic reranker combines."""

    vector_scores: dict[uuid.UUID, float]
    bm25_scores: dict[uuid.UUID, float]
    lexical_query: str | None = None


@dataclass(frozen=True)
class RerankOutcome:
    results: list[ScoredEmbedding]
    strategy_requested: str
    strategy_applied: str
    model: str
    duration_ms: float
    fallback_reason: str | None = None


class Reranker(Protocol):
    name: str
    model: str

    async def rerank(
        self,
        query: str,
        candidates: list[ScoredEmbedding],
        *,
        signals: RerankSignals,
        top_k: int,
    ) -> list[ScoredEmbedding]: ...


class RerankerUnavailableError(RuntimeError):
    """The strategy cannot run in this process (missing optional dependency)."""


def embedding_tiebreak_key(embedding: Embedding) -> tuple[str, int, str]:
    """Deterministic secondary key for equal-score ordering."""
    meta = embedding.metadata_json or {}
    chunk_index = meta.get("chunk_index", -1)
    if not isinstance(chunk_index, int):
        chunk_index = -1
    return (str(embedding.document_id), chunk_index, str(embedding.id))


def lexical_overlap_score(query: str, chunk_text: str) -> float:
    query_tokens = token_set(query)
    if not query_tokens:
        return 0.0
    chunk_tokens = token_set(chunk_text or "")
    if not chunk_tokens:
        return 0.0
    overlap = len(query_tokens & chunk_tokens)
    return overlap / len(query_tokens)


def _sort_by_score(
    scored: list[ScoredEmbedding], signals: RerankSignals
) -> list[ScoredEmbedding]:
    return sorted(
        scored,
        key=lambda item: (
            -item[1],
            -signals.vector_scores.get(item[0].id, 0.0),
            -signals.bm25_scores.get(item[0].id, 0.0),
            embedding_tiebreak_key(item[0]),
        ),
    )


def rerank_candidates(
    query: str,
    candidates: list[ScoredEmbedding],
    *,
    vector_scores: dict[uuid.UUID, float] | None = None,
    bm25_scores: dict[uuid.UUID, float] | None = None,
    lexical_query: str | None = None,
    top_k: int,
) -> list[ScoredEmbedding]:
    """Heuristic reranking: weighted lexical + vector + BM25 + RRF blend.

    lexical_query: when the user query is non-EN, pass the EN rewrite here so
    that the lexical overlap operates against English corpus text instead of
    always returning ~0 for non-ASCII queries.
    """
    if not candidates:
        return []

    signals = RerankSignals(
        vector_scores=vector_scores or {},
        bm25_scores=bm25_scores or {},
        lexical_query=lexical_query,
    )
    max_rrf = max(score for _, score in candidates)
    effective_lexical_query = lexical_query or query

    rescored: list[ScoredEmbedding] = []
    for embedding, rrf_score in candidates:
        lexical_score = lexical_overlap_score(effective_lexical_query, embedding.chunk_text or "")
        vector_score = signals.vector_scores.get(embedding.id, 0.0)
        bm25_score = signals.bm25_scores.get(embedding.id, 0.0)
        normalized_rrf = rrf_score / max_rrf if max_rrf else 0.0
        final_score = (
            (lexical_score * RERANK_LEXICAL_WEIGHT)
            + (vector_score * RERANK_VECTOR_WEIGHT)
            + (bm25_score * RERANK_BM25_WEIGHT)
            + (normalized_rrf * RERANK_RRF_WEIGHT)
        )
        rescored.append((embedding, round(final_score, 6)))

    return _sort_by_score(rescored, signals)[:top_k]


class HeuristicReranker:
    name = RerankerStrategy.heuristic.value
    model = HEURISTIC_MODEL_NAME

    async def rerank(
        self,
        query: str,
        candidates: list[ScoredEmbedding],
        *,
        signals: RerankSignals,
        top_k: int,
    ) -> list[ScoredEmbedding]:
        return rerank_candidates(
            query,
            candidates,
            vector_scores=signals.vector_scores,
            bm25_scores=signals.bm25_scores,
            lexical_query=signals.lexical_query,
            top_k=top_k,
        )


def _passage(embedding: Embedding) -> str:
    text = " ".join((embedding.chunk_text or "").split())
    return text[:RERANKER_PASSAGE_CHARS]


def _blend_semantic_scores(
    heuristic_pool: list[ScoredEmbedding],
    semantic_scores: list[float],
    *,
    signals: RerankSignals,
    top_k: int,
) -> list[ScoredEmbedding]:
    blended = [
        (
            embedding,
            round(
                semantic * SEMANTIC_SCORE_WEIGHT + heuristic * HEURISTIC_SCORE_WEIGHT,
                6,
            ),
        )
        for (embedding, heuristic), semantic in zip(heuristic_pool, semantic_scores, strict=True)
    ]
    return _sort_by_score(blended, signals)[:top_k]


class _SemanticReranker:
    """Shared shape: heuristic pre-pass over the pool, then semantic scoring."""

    name: str
    model: str

    async def score(self, query: str, passages: list[str]) -> list[float]:
        raise NotImplementedError

    async def rerank(
        self,
        query: str,
        candidates: list[ScoredEmbedding],
        *,
        signals: RerankSignals,
        top_k: int,
    ) -> list[ScoredEmbedding]:
        if not candidates:
            return []
        heuristic_pool = rerank_candidates(
            query,
            candidates,
            vector_scores=signals.vector_scores,
            bm25_scores=signals.bm25_scores,
            lexical_query=signals.lexical_query,
            top_k=RERANKER_MAX_CANDIDATES,
        )
        semantic_scores = await self.score(query, [_passage(emb) for emb, _ in heuristic_pool])
        return _blend_semantic_scores(
            heuristic_pool, semantic_scores, signals=signals, top_k=top_k
        )


LLM_RERANK_SYSTEM_PROMPT = (
    "You grade how well each passage answers a user's question for a product "
    "support knowledge base. Passages are numbered. For every passage return an "
    "integer relevance score from 0 (unrelated) to 10 (directly and completely "
    "answers the question). Judge by meaning, not by shared words; the passage "
    "may be in a different language than the question.\n"
    'Respond with JSON only: {"scores": {"<passage number>": <score>, ...}}.'
)


def _parse_llm_scores(raw: str, count: int) -> list[float]:
    payload = json.loads(raw)
    scores = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(scores, dict):
        raise ValueError("missing scores object")
    parsed: list[float] = []
    for index in range(1, count + 1):
        value = scores.get(str(index), 0)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        parsed.append(min(max(numeric, 0.0), LLM_SCORE_SCALE) / LLM_SCORE_SCALE)
    return parsed


class LLMReranker(_SemanticReranker):
    name = RerankerStrategy.llm.value

    def __init__(self, *, api_key: str, tenant_id: uuid.UUID | None = None) -> None:
        self._api_key = api_key
        self._tenant_id = tenant_id
        self.model = settings.reranker_llm_model

    async def score(self, query: str, passages: list[str]) -> list[float]:
        client = get_async_openai_client(
            self._api_key, timeout=settings.reranker_timeout_seconds
        )
        numbered = "\n\n".join(
            f"[{index}] {passage}" for index, passage in enumerate(passages, start=1)
        )
        response = await async_call_openai_with_retry(
            "rerank_candidates_llm",
            lambda: client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": LLM_RERANK_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Question: {query}\n\nPassages:\n{numbered}",
                    },
                ],
                temperature=0,
                max_completion_tokens=8 * len(passages) + 20,
                response_format={"type": "json_object"},
            ),
            tenant_id=str(self._tenant_id) if self._tenant_id else None,
            max_attempts=1,
        )
        return _parse_llm_scores(response.choices[0].message.content or "", len(passages))


_cross_encoder_lock = threading.Lock()
_cross_encoder_models: dict[str, Any] = {}


def _load_cross_encoder(model_name: str) -> Any:
    """Load the model once per process; import lazily so the dependency stays optional."""
    cached = _cross_encoder_models.get(model_name)
    if cached is not None:
        return cached
    # Non-blocking: a cold load can take minutes (first download), and a turn
    # that would wait on it has already been abandoned by the timeout. Parking
    # thread-pool workers behind the lock would starve the rest of the chat path.
    if not _cross_encoder_lock.acquire(blocking=False):
        raise RerankerUnavailableError("cross-encoder is still loading")
    try:
        cached = _cross_encoder_models.get(model_name)
        if cached is not None:
            return cached
        try:
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RerankerUnavailableError("sentence-transformers is not installed") from exc
        # Force raw logits: the library's default activation for single-label
        # models differs across versions (sigmoid in 3.x, identity in 5.x), and
        # the sigmoid must be applied exactly once to land on the 0-1 scale.
        activation_kwarg = (
            "activation_fn"
            if "activation_fn" in inspect.signature(CrossEncoder.__init__).parameters
            else "default_activation_function"
        )
        model = CrossEncoder(
            model_name, max_length=512, **{activation_kwarg: torch.nn.Identity()}
        )
        _cross_encoder_models[model_name] = model
        return model
    finally:
        _cross_encoder_lock.release()


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


class CrossEncoderReranker(_SemanticReranker):
    name = RerankerStrategy.cross_encoder.value

    def __init__(self) -> None:
        self.model = settings.reranker_cross_encoder_model

    async def score(self, query: str, passages: list[str]) -> list[float]:
        loop = asyncio.get_running_loop()

        def _predict() -> list[float]:
            model = _load_cross_encoder(self.model)
            logits = model.predict([(query, passage) for passage in passages])
            return [_sigmoid(float(value)) for value in logits]

        return await loop.run_in_executor(None, _predict)


def build_reranker(
    strategy: str,
    *,
    api_key: str | None,
    tenant_id: uuid.UUID | None = None,
) -> Reranker:
    if strategy == RerankerStrategy.llm.value:
        if not api_key:
            raise RerankerUnavailableError("tenant has no OpenAI key")
        return LLMReranker(api_key=api_key, tenant_id=tenant_id)
    if strategy == RerankerStrategy.cross_encoder.value:
        return CrossEncoderReranker()
    return HeuristicReranker()


async def rerank_with_fallback(
    query: str,
    candidates: list[ScoredEmbedding],
    *,
    strategy: str,
    signals: RerankSignals,
    top_k: int,
    api_key: str | None,
    tenant_id: uuid.UUID | None = None,
) -> RerankOutcome:
    """Run the tenant's strategy under a hard timeout; fall back to the heuristic.

    The heuristic is never skipped on failure and never times out itself, so
    the outcome always carries a ranked list.
    """
    started_at = perf_counter()
    heuristic = HeuristicReranker()
    fallback_reason: str | None = None

    if strategy != RerankerStrategy.heuristic.value:
        try:
            reranker = build_reranker(strategy, api_key=api_key, tenant_id=tenant_id)
            results = await asyncio.wait_for(
                reranker.rerank(query, candidates, signals=signals, top_k=top_k),
                timeout=settings.reranker_timeout_seconds,
            )
            return RerankOutcome(
                results=results,
                strategy_requested=strategy,
                strategy_applied=strategy,
                model=reranker.model,
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
            )
        except TimeoutError:
            fallback_reason = "timeout"
        except RerankerUnavailableError as exc:
            fallback_reason = f"unavailable: {exc}"
        except Exception as exc:
            fallback_reason = f"error: {type(exc).__name__}"
        logger.warning(
            "reranker_fallback",
            extra={
                "strategy": strategy,
                "reason": fallback_reason,
                "tenant_id": str(tenant_id) if tenant_id else None,
                "elapsed_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        )

    results = await heuristic.rerank(query, candidates, signals=signals, top_k=top_k)
    return RerankOutcome(
        results=results,
        strategy_requested=strategy,
        strategy_applied=heuristic.name,
        model=heuristic.model,
        duration_ms=round((perf_counter() - started_at) * 1000, 2),
        fallback_reason=fallback_reason,
    )
