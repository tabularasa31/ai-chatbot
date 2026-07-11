"""Chat RAG pipeline orchestrator.

``async_run_chat_pipeline`` is the pure (side-effect-free towards the DB)
async pipeline behind every RAG chat turn. It wires the step functions from
``backend/chat/steps/`` into a flat, top-down flow::

    prepare_turn                # tenant profile, KB scripts, dialog context
    injection_guard             # guard 1: structural → semantic (2 levels)
    launch_concurrent_tasks     # relevance guard ∥ embeddings ∥ query rewrite
    build_query_plan            # collect variants + embed
    match_faq                   # faq_direct short-circuit
    start_speculative_retrieval # retrieval ∥ relevance-guard wait
    relevance_guard             # guard 2: off-topic / social / complaint
    load_generation_inputs      # prompt hints + quick answers
    run_retrieval               # pgvector + BM25 + RRF (or consume speculative)
    zero_hits_fast_path         # guard 3a: strict zero-hits routing
    low_retrieval_guard         # guard 3b: all-similarities-below-threshold
    run_generation              # LLM answer + language-mismatch retry

Each step takes a :class:`~backend.chat.types.PipelineRun` and returns a
terminal :class:`~backend.chat.types.ChatPipelineResult` to short-circuit
(reject paths, ``faq_direct``) or ``None`` to continue. Concurrency lives
*inside* the steps (tasks stored on ``run.state``), so the flow above stays
readable without giving up the guard/retrieval overlap.

Every step is covered by a Langfuse span (created either here in the step
modules or inside the guard/search helpers themselves), so a single trace
shows exactly which step a turn failed or short-circuited on.

Guards and embeddings run as ``asyncio.create_task``, so they do not tie up
OS threads. On injection detection the LLM-backed tasks are never launched;
on relevance reject the speculative retrieval task is cancelled.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat.language import ResolvedLanguageContext
from backend.chat.steps import generate, pre_retrieval, retrieval
from backend.chat.types import ChatPipelineResult, PipelineRun
from backend.models import Chat, TenantProfile
from backend.observability import TraceHandle

logger = logging.getLogger(__name__)


async def async_run_chat_pipeline(
    tenant_id: uuid.UUID,
    question: str,
    db: AsyncSession,
    *,
    api_key: str,
    language_context: ResolvedLanguageContext | None = None,
    user_context_line: str | None = None,
    disclosure_config: dict[str, Any] | None = None,
    trace: TraceHandle | None = None,
    tenant_public_id: str | None = None,
    bot_public_id: str | None = None,
    retry_bot_id: str | None = None,
    chat_id: str | None = None,
    chat: Chat | None = None,
    stream_callback: Callable[[str], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    agent_instructions: str | None = None,
    allow_clarification: bool = True,
    guard_profile: TenantProfile | None = None,
    support_contact_question: bool = False,
) -> ChatPipelineResult:
    """Run one chat turn through the RAG pipeline (see module docstring)."""
    # The language context is resolved by the caller on the normal chat path;
    # this fallback covers direct pipeline invocations (evals, tests).
    if language_context is None:
        from backend.chat import service as _svc

        language_context = _svc._resolve_chat_language_context(
            current_turn_text=question,
            tenant_row=None,
            tenant_profile=None,
            is_bootstrap_turn=_svc._is_bootstrap_question(question),
            bootstrap_user_locale=None,
            browser_locale=None,
        )

    run = PipelineRun(
        tenant_id=tenant_id,
        question=question,
        db=db,
        api_key=api_key,
        language_context=language_context,
        user_context_line=user_context_line,
        disclosure_config=disclosure_config,
        trace=trace,
        tenant_public_id=tenant_public_id,
        bot_public_id=bot_public_id,
        retry_bot_id=retry_bot_id,
        chat_id=chat_id,
        chat=chat,
        stream_callback=stream_callback,
        status_callback=status_callback,
        agent_instructions=agent_instructions,
        allow_clarification=allow_clarification,
        guard_profile=guard_profile,
        support_contact_question=support_contact_question,
    )

    # --- Pre-retrieval: guards, query plan, FAQ --------------------------
    await pre_retrieval.prepare_turn(run)

    early = await pre_retrieval.injection_guard(run)
    if early is not None:
        return early

    # Relevance guard, base embedding and semantic rewrite start here and
    # overlap; the guard verdict is awaited only in relevance_guard below.
    pre_retrieval.launch_concurrent_tasks(run)
    await pre_retrieval.build_query_plan(run)

    early = await pre_retrieval.match_faq(run)
    if early is not None:
        return early

    # Retrieval overlaps the 2-10 s relevance-guard wait; cancelled on reject.
    pre_retrieval.start_speculative_retrieval(run)

    early = await pre_retrieval.relevance_guard(run)
    if early is not None:
        return early

    await pre_retrieval.load_generation_inputs(run)

    # --- Retrieval + retrieval-quality guards -----------------------------
    await retrieval.run_retrieval(run)

    early = await retrieval.zero_hits_fast_path(run)
    if early is not None:
        return early

    early = await retrieval.low_retrieval_guard(run)
    if early is not None:
        return early

    # --- Generation -------------------------------------------------------
    # Release the DB connection before the LLM call. run_generation does not
    # touch the DB; holding a connection open for 20-30 s of LLM latency
    # exhausts the pool under any meaningful concurrency.
    #
    # close() is used instead of rollback() because the aiosqlite driver
    # routes rollback() through await_only() (the greenlet sync bridge), which
    # raises MissingGreenlet when called from a pure async context. close()
    # releases the connection without sending any DB command.
    #
    # In SQLAlchemy 2.0, AsyncSession.close() leaves the session reusable — it
    # re-acquires a connection automatically when the handler writes the result.
    await db.close()
    return await generate.run_generation(run)
