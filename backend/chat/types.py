"""Shared typed I/O for the chat RAG pipeline.

Holds the dataclasses that flow between pipeline steps
(``backend/chat/steps/``), the orchestrator (``backend/chat/pipeline.py``)
and the handler (``backend/chat/handlers/rag.py``):

* :class:`RetrievalContext` — output of the retrieval step.
* :class:`ChatPipelineResult` — terminal result of the whole pipeline.
* :class:`PipelineState` — mutable state one step produces and a later step
  consumes.
* :class:`PipelineRun` — read-only per-turn inputs + the mutable state,
  passed to every step function.

Nothing here performs I/O; keep it that way so steps stay independently
testable.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat.language import ResolvedLanguageContext
from backend.faq.faq_matcher import FAQMatchResult, FAQRow
from backend.guards.types import Verdict
from backend.models import Chat, TenantProfile
from backend.observability import TraceHandle
from backend.search.service import (
    RetrievalReliability,
    default_retrieval_reliability,
)

RejectReasonLiteral = Literal[
    "injection",
    "not_relevant",
    "low_retrieval",
    "rephrase",
    "social",
    "social_question",
]


@dataclass
class RetrievalContext:
    """Retrieved chunks plus the confidence signal used outside ranking."""

    chunk_texts: list[str]
    document_ids: list[uuid.UUID]
    scores: list[float]
    mode: Literal["vector", "hybrid", "none"]
    best_rank_score: float | None
    best_confidence_score: float | None
    confidence_source: Literal["vector_similarity", "none"]
    reliability: RetrievalReliability = field(default_factory=default_retrieval_reliability)
    variant_mode: Literal["single", "multi"] = "single"
    query_variant_count: int = 1
    extra_embedded_queries: int = 0
    extra_embedding_api_requests: int = 0
    extra_vector_search_calls: int = 0
    bm25_expansion_mode: Literal["asymmetric", "symmetric_variants"] = "asymmetric"
    bm25_query_variant_count: int = 1
    bm25_variant_eval_count: int = 1
    extra_bm25_variant_evals: int = 0
    bm25_merged_hit_count_before_cap: int = 0
    bm25_merged_hit_count_after_cap: int = 0
    retrieval_duration_ms: float = 0.0
    vector_similarities: list[float | None] | None = None


def _empty_retrieval_context() -> RetrievalContext:
    """A no-hits RetrievalContext for pipeline exits that skip retrieval but
    must still flow through the escalation branch of ``RagHandler._handle_sync``
    (which dereferences ``result.retrieval``)."""
    return RetrievalContext(
        chunk_texts=[],
        document_ids=[],
        scores=[],
        mode="none",
        best_rank_score=None,
        best_confidence_score=None,
        confidence_source="none",
    )


@dataclass
class ChatPipelineResult:
    """
    Result of the pure RAG pipeline, with no side effects.

    Blocks:
      user_output  — raw_answer, final_answer, tokens_used
      decision     — strategy, reject_reason, flags
      retrieval    — full RetrievalContext (None for guard_reject / faq_direct)
      escalation   — recommended flag + trigger (compute only, no ticket created)
      debug        — faq_match result for diagnostic use
    """

    # user_output
    raw_answer: str
    final_answer: str
    tokens_used: int
    # decision
    strategy: Literal["faq_direct", "faq_context", "rag_only", "guard_reject"]
    reject_reason: RejectReasonLiteral | None
    is_reject: bool
    is_faq_direct: bool
    # retrieval
    retrieval: RetrievalContext | None
    # escalation (pure computation, no side effects)
    escalation_recommended: bool
    escalation_trigger: Any  # EscalationTrigger | None
    # Language-agnostic signal from the LLM: True when the generated answer
    # ended with the OFFER_MARKER sentinel, meaning the LLM offered to open
    # a support ticket. Surfaced so _handle_sync can arm pre_confirm even
    # when decide()'s confidence classification disagreed with the LLM's
    # self-assessment that it couldn't answer from the docs.
    llm_offered_ticket: bool = False
    # pipeline timing (ms); 0 means the stage was skipped
    retrieval_ms: int = 0
    llm_ms: int = 0
    # Language-mismatch retry: a SECOND full async_generate_answer call when the
    # first answer came back in the wrong language. Tracked separately so the
    # PostHog `chat.turn` event reports honest LLM wall time without losing
    # visibility into how often the retry actually fires.
    llm_lang_retry_ms: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    # debug extras
    faq_match: Any = None  # FAQMatchResult | None
    # language_context is always populated by async_run_chat_pipeline; None
    # only for callers that construct ChatPipelineResult directly without it.
    language_context: ResolvedLanguageContext | None = None
    # cross-lingual retrieval telemetry (populated only on the RAG path)
    query_script: str | None = None
    kb_scripts: list[str] | None = None
    cross_lingual_triggered: bool = False
    cross_lingual_variants_count: int = 0
    query_kb_language_match: str | None = None
    retrieval_used_cross_lingual_variant: bool = False


@dataclass
class PipelineState:
    """Mutable state shared across the pipeline steps.

    Read-only inputs (``tenant_id``, ``api_key``, callbacks, …) live on
    :class:`PipelineRun`; this dataclass only holds values that one step
    *produces* and a later step *consumes*.
    """

    # Pre-retrieval outputs
    query_script: str = ""
    kb_scripts: list[str] = field(default_factory=list)
    cross_lingual_triggered: bool = False
    cross_lingual_variants_added: int = 0
    query_kb_language_match: Literal["native", "mismatch", "unknown"] = "unknown"
    query_variants: list[str] = field(default_factory=list)
    variant_vectors: list[list[float]] = field(default_factory=list)
    embed_api_request_count: int = 0
    rewritten_variant: str | None = None
    query_rewrite_skip_reason: str | None = None
    faq_match: FAQMatchResult | None = None
    # Dialog tail rendered once per turn; shared by the semantic query rewrite
    # (continuation resolution), the relevance guard (pre-retrieval step) and
    # the consecutive-zero-hits force check (retrieval step) so all consumers
    # see the same context and the guard verdicts share a cache entry.
    guard_dialog_context: str | None = None
    # True when the pre-retrieval relevance guard skipped the LLM via the
    # short-query bypass (≤ SHORT_QUERY_WORD_LIMIT words) and so never produced
    # a real category verdict. The zero-hits fast path reads this to classify a
    # short bot-meta / social turn on the *first* turn instead of only catching
    # it on the second consecutive zero-hit turn's force check.
    guard_bypassed_short_query: bool = False
    profile: TenantProfile | None = None
    client_product_name: str | None = None
    topic_hint: str | None = None
    faq_context_items: list[FAQRow] | None = None
    quick_answer_items: list[str] = field(default_factory=list)
    strategy: Literal["faq_direct", "faq_context", "rag_only", "guard_reject"] = "rag_only"

    # Guard profile resolved by prepare_turn (preloaded by the caller or
    # fetched from the DB); consumed by the relevance guard launch and its
    # cancelled-fallback path.
    guard_profile: TenantProfile | None = None
    # Base (pre-rewrite) query variants from expand_query.
    base_query_variants: list[str] = field(default_factory=list)

    # In-flight concurrent tasks: created by launch_concurrent_tasks, consumed
    # by later steps. Storing them on state (rather than closure variables)
    # is what lets the steps live in separate modules while preserving the
    # overlap between the relevance guard, embeddings and the rewrite calls.
    rel_task: asyncio.Task[Verdict] | None = None
    rel_started_at: float = 0.0
    base_embed_task: asyncio.Task[list[list[float]]] | None = None
    rewrite_task: asyncio.Task[str | None] | None = None
    cross_lingual_tasks: list[asyncio.Task[str | None]] = field(default_factory=list)

    # Retrieval outputs
    retrieval: RetrievalContext | None = None
    reranker_rescued: bool = False

    # Speculative retrieval: started concurrently with the relevance guard and
    # consumed in the retrieval step. Cancelled/discarded if the guard rejects.
    retrieve_kwargs: dict[str, Any] = field(default_factory=dict)
    spec_retrieval_task: asyncio.Task[RetrievalContext] | None = None


@dataclass
class PipelineRun:
    """One chat turn flowing through the pipeline: inputs + mutable state.

    Every step function takes a ``PipelineRun`` and either returns a terminal
    :class:`ChatPipelineResult` (short-circuit) or ``None`` (continue).
    """

    tenant_id: uuid.UUID
    question: str
    db: AsyncSession
    api_key: str
    language_context: ResolvedLanguageContext
    user_context_line: str | None = None
    disclosure_config: dict[str, Any] | None = None
    trace: TraceHandle | None = None
    tenant_public_id: str | None = None
    bot_public_id: str | None = None
    retry_bot_id: str | None = None
    chat_id: str | None = None
    chat: Chat | None = None
    stream_callback: Callable[[str], None] | None = None
    status_callback: Callable[[str], None] | None = None
    agent_instructions: str | None = None
    allow_clarification: bool = True
    guard_profile: TenantProfile | None = None
    support_contact_question: bool = False
    state: PipelineState = field(default_factory=PipelineState)
