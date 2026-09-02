"""Answer cache: exact level (Redis) + semantic level (pgvector) in front of
retrieval and generation. See backend/chat/answer_cache.py."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat import answer_cache
from backend.chat.answer_cache import (
    AnswerCacheCandidate,
    AnswerCacheHit,
    AnswerCacheScope,
    CachedAnswer,
    normalize_question,
)
from backend.chat.language import ResolvedLanguageContext
from backend.chat.service import RetrievalContext, process_chat_message
from backend.chat.steps import answer_cache as cache_steps
from backend.chat.types import ChatPipelineResult, PipelineRun
from backend.core import redis as redis_mod
from backend.core.config import settings
from backend.faq.faq_matcher import FAQMatchResult
from backend.models import (
    AnswerCacheEntry,
    Bot,
    Chat,
    Document,
    DocumentStatus,
    DocumentType,
    Tenant,
)
from backend.models.base import _utcnow
from backend.observability import cache_metrics
from backend.search.service import build_reliability_assessment
from tests._async_utils import as_async as _as_async
from tests.test_rag_pipeline import _create_client, _insert_single_chunk


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str, ttl: int) -> bool:
        store[key] = value
        return True

    async def _incr(key: str) -> int:
        store[key] = str(int(store.get(key, "0")) + 1)
        return int(store[key])

    monkeypatch.setattr(redis_mod, "is_enabled", lambda: True)
    monkeypatch.setattr(redis_mod, "cache_get", _get)
    monkeypatch.setattr(redis_mod, "cache_set_with_ttl", _set)
    monkeypatch.setattr(redis_mod, "cache_incr", _incr)
    return store


@pytest.fixture(autouse=True)
def _reset_cache_metrics():
    cache_metrics.reset()
    yield
    cache_metrics.reset()


def _language_context(language: str = "en") -> ResolvedLanguageContext:
    return ResolvedLanguageContext(
        detected_language=language,
        confidence=0.99,
        is_reliable=True,
        response_language=language,
        response_language_resolution_reason="detected",
        escalation_language=language,
        escalation_language_source="default",
    )


def _scope(**overrides) -> AnswerCacheScope:
    question = normalize_question(overrides.pop("question", "How do I reset my password?"))
    defaults = dict(
        tenant_id=uuid.uuid4(),
        bot_id=uuid.uuid4(),
        kb_fingerprint="fp-1",
        response_language="en",
        question=question,
        question_hash=answer_cache.question_hash(question),
    )
    defaults.update(overrides)
    return AnswerCacheScope(**defaults)


def _cached_answer(**overrides) -> CachedAnswer:
    defaults = dict(
        answer="Open Settings → Security → Reset password.",
        strategy="rag_only",
        document_ids=(str(uuid.uuid4()),),
        scores=(0.91,),
        chunk_texts=("Reset password help",),
        retrieval_mode="hybrid",
        best_rank_score=0.91,
        best_confidence_score=0.88,
        confidence_source="vector_similarity",
        reliability_score="high",
        retrieval_ms=340,
        llm_ms=4200,
        created_at=_utcnow().isoformat(),
    )
    defaults.update(overrides)
    return CachedAnswer(**defaults)


def _make_run(*, eligible: bool = True, question: str = "How do I reset my password?") -> PipelineRun:
    return PipelineRun(
        tenant_id=uuid.uuid4(),
        question=question,
        db=None,
        api_key="test-key",
        language_context=_language_context(),
        retry_bot_id=str(uuid.uuid4()),
        answer_cache_eligible=eligible,
    )


def _retrieval(**overrides) -> RetrievalContext:
    defaults = dict(
        chunk_texts=["Reset password help"],
        document_ids=[uuid.uuid4()],
        scores=[0.9],
        mode="hybrid",
        best_rank_score=0.9,
        best_confidence_score=0.88,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.9, result_count=2),
    )
    defaults.update(overrides)
    return RetrievalContext(**defaults)


def _generated_result(retrieval: RetrievalContext | None = None, **overrides) -> ChatPipelineResult:
    defaults = dict(
        raw_answer="Answer text",
        final_answer="Answer text",
        tokens_used=50,
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        is_faq_direct=False,
        retrieval=retrieval if retrieval is not None else _retrieval(),
        escalation_recommended=False,
        escalation_trigger=None,
        retrieval_ms=300,
        llm_ms=4000,
    )
    defaults.update(overrides)
    return ChatPipelineResult(**defaults)


# ---------------------------------------------------------------------------
# Normalization and payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  How do I   reset my password?  ", "how do i reset my password"),
        ("HOW DO I RESET MY PASSWORD", "how do i reset my password"),
        ("¿Cómo restablezco mi contraseña?", "cómo restablezco mi contraseña"),
        ("Как сбросить пароль?!", "как сбросить пароль"),
        ("パスワードをリセットするには？", "パスワードをリセットするには"),
    ],
)
def test_normalize_question_is_script_agnostic(raw: str, expected: str) -> None:
    assert normalize_question(raw) == expected


def test_cached_answer_round_trips_through_json() -> None:
    cached = _cached_answer()
    restored = CachedAnswer.from_payload(cached.to_json())
    assert restored == cached
    assert restored.saved_ms == 340 + 4200
    assert CachedAnswer.from_payload(cached.to_dict()) == cached


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"v": 99, "answer": "x", "strategy": "rag_only"}',
        '{"v": 1, "answer": "x", "strategy": "rag_only", "document_ids": ["nope"]}',
        '{"v": 1, "strategy": "rag_only"}',
    ],
)
def test_cached_answer_rejects_malformed_payload(payload: str) -> None:
    assert CachedAnswer.from_payload(payload) is None


def test_exact_key_separates_bot_language_and_fingerprint() -> None:
    base = _scope()
    assert base.exact_key == _scope(tenant_id=base.tenant_id, bot_id=base.bot_id).exact_key
    assert base.exact_key != _scope(tenant_id=base.tenant_id).exact_key  # other bot
    assert (
        base.exact_key
        != _scope(tenant_id=base.tenant_id, bot_id=base.bot_id, response_language="de").exact_key
    )
    assert (
        base.exact_key
        != _scope(tenant_id=base.tenant_id, bot_id=base.bot_id, kb_fingerprint="fp-2").exact_key
    )


# ---------------------------------------------------------------------------
# Exact level (Redis)
# ---------------------------------------------------------------------------


def test_exact_lookup_serves_cached_answer_without_pipeline_work(fake_redis: dict[str, str]) -> None:
    run = _make_run()
    scope = _scope(tenant_id=run.tenant_id)
    run.state.answer_cache_scope = scope
    cached = _cached_answer()
    fake_redis[scope.exact_key] = cached.to_json()

    result = asyncio.run(cache_steps.exact_lookup(run))

    assert result is not None
    assert result.final_answer == cached.answer
    assert result.tokens_used == 0
    assert result.llm_ms == 0 and result.retrieval_ms == 0
    assert result.answer_cache is not None
    assert result.answer_cache.level == "exact"
    assert result.answer_cache.saved_ms == cached.saved_ms
    assert result.retrieval is not None
    assert [str(d) for d in result.retrieval.document_ids] == list(cached.document_ids)
    assert result.retrieval.chunk_texts == list(cached.chunk_texts)
    assert result.retrieval.reliability.score == "high"
    assert result.escalation_recommended is False
    assert cache_metrics.snapshot()["answer_exact"]["hits"] == 1


def test_exact_lookup_misses_on_empty_store(fake_redis: dict[str, str]) -> None:
    run = _make_run()
    run.state.answer_cache_scope = _scope(tenant_id=run.tenant_id)
    assert asyncio.run(cache_steps.exact_lookup(run)) is None
    assert cache_metrics.snapshot()["answer_exact"]["misses"] == 1


def test_exact_lookup_skipped_without_scope() -> None:
    run = _make_run()
    assert asyncio.run(cache_steps.exact_lookup(run)) is None
    assert "answer_exact" not in cache_metrics.snapshot()


def test_exact_lookup_with_redis_disabled_is_a_silent_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis_mod, "is_enabled", lambda: False)
    run = _make_run()
    run.state.answer_cache_scope = _scope(tenant_id=run.tenant_id)
    assert asyncio.run(cache_steps.exact_lookup(run)) is None
    assert "answer_exact" not in cache_metrics.snapshot()


def test_exact_lookup_records_span_and_stage(fake_redis: dict[str, str]) -> None:
    class _Span:
        def __init__(self) -> None:
            self.ended: dict = {}

        def end(self, **kwargs) -> None:
            self.ended = kwargs

    class _Trace:
        def __init__(self) -> None:
            self.spans: list[tuple[dict, _Span]] = []
            self.stages: dict[str, float] = {}

        def span(self, **kwargs):
            span = _Span()
            self.spans.append((kwargs, span))
            return span

        def record_stage_ms(self, stage: str, ms: float) -> None:
            self.stages[stage] = ms

    trace = _Trace()
    run = _make_run()
    run.trace = trace  # type: ignore[assignment]
    scope = _scope(tenant_id=run.tenant_id)
    run.state.answer_cache_scope = scope
    fake_redis[scope.exact_key] = _cached_answer().to_json()

    asyncio.run(cache_steps.exact_lookup(run))

    (kwargs, span), = trace.spans
    assert kwargs["name"] == "answer-cache"
    assert kwargs["input"] == {"level": "exact"}
    assert span.ended["output"]["hit"] is True
    assert span.ended["output"]["saved_ms"] == 340 + 4200
    assert "answer_cache_ms" in trace.stages


# ---------------------------------------------------------------------------
# Scope resolution / fingerprint
# ---------------------------------------------------------------------------


def _tenant_with_doc(db: Session) -> tuple[Tenant, Document]:
    tenant_row = Tenant(name="Cache Tenant")
    db.add(tenant_row)
    db.commit()
    doc = Document(
        tenant_id=tenant_row.id,
        filename="a.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db.add(doc)
    db.commit()
    return tenant_row, doc


async def _resolve(tenant_id: uuid.UUID, db, **overrides) -> AnswerCacheScope | None:
    kwargs = dict(
        tenant_id=tenant_id,
        bot_id=None,
        response_language="en",
        question="How do I reset my password?",
        agent_instructions="Be brief.",
        disclosure_config=None,
        db=db,
    )
    kwargs.update(overrides)
    return await answer_cache.resolve_scope(**kwargs)


@pytest.mark.asyncio
async def test_fingerprint_tracks_documents_instructions_and_generation(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, doc = _tenant_with_doc(db_session)
    baseline = await _resolve(tenant_row.id, async_search_session)
    assert baseline is not None
    assert baseline.question == "how do i reset my password"

    again = await _resolve(tenant_row.id, async_search_session)
    assert again.kb_fingerprint == baseline.kb_fingerprint

    changed_instructions = await _resolve(
        tenant_row.id, async_search_session, agent_instructions="Be verbose."
    )
    assert changed_instructions.kb_fingerprint != baseline.kb_fingerprint

    changed_disclosure = await _resolve(
        tenant_row.id, async_search_session, disclosure_config={"level": "strict"}
    )
    assert changed_disclosure.kb_fingerprint != baseline.kb_fingerprint

    db_session.add(
        Document(
            tenant_id=tenant_row.id,
            filename="b.md",
            file_type=DocumentType.markdown,
            status=DocumentStatus.ready,
            parsed_text="more",
        )
    )
    db_session.commit()
    after_upload = await _resolve(tenant_row.id, async_search_session)
    assert after_upload.kb_fingerprint != baseline.kb_fingerprint

    db_session.delete(doc)
    db_session.commit()
    after_delete = await _resolve(tenant_row.id, async_search_session)
    assert after_delete.kb_fingerprint not in {baseline.kb_fingerprint, after_upload.kb_fingerprint}

    await answer_cache.invalidate_tenant(tenant_row.id)
    after_reindex = await _resolve(tenant_row.id, async_search_session)
    assert after_reindex.kb_fingerprint != after_delete.kb_fingerprint


@pytest.mark.asyncio
async def test_resolve_scope_disabled_when_ttl_is_zero(
    db_session: Session, async_search_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    monkeypatch.setattr(settings, "answer_cache_ttl_seconds", 0)
    assert await _resolve(tenant_row.id, async_search_session) is None


def test_resolve_scope_step_respects_eligibility() -> None:
    run = _make_run(eligible=False)
    asyncio.run(cache_steps.resolve_scope(run))
    assert run.state.answer_cache_scope is None


def test_invalidate_tenant_sync_is_noop_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis_mod, "is_enabled", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(redis_mod, "cache_incr_sync", lambda key, **kw: calls.append(key))
    answer_cache.invalidate_tenant_sync(uuid.uuid4())
    answer_cache.invalidate_tenant_sync(None)
    assert calls == []


# ---------------------------------------------------------------------------
# Semantic level (pgvector; Python cosine fallback on SQLite)
# ---------------------------------------------------------------------------


def _unit(x: float, y: float, z: float) -> list[float]:
    """Unit vector in the first three of the model's 1536 dimensions."""
    norm = (x * x + y * y + z * z) ** 0.5
    return [x / norm, y / norm, z / norm] + [0.0] * 1533


def _insert_entry(
    db: Session,
    scope: AnswerCacheScope,
    embedding: list[float],
    *,
    cached: CachedAnswer | None = None,
    expires_in: timedelta = timedelta(hours=1),
) -> AnswerCacheEntry:
    now = _utcnow()
    entry = AnswerCacheEntry(
        tenant_id=scope.tenant_id,
        bot_id=scope.bot_id,
        kb_fingerprint=scope.kb_fingerprint,
        response_language=scope.response_language,
        question_hash=scope.question_hash,
        question=scope.question,
        question_embedding=embedding,
        payload=(cached or _cached_answer()).to_dict(),
        created_at=now,
        expires_at=now + expires_in,
    )
    db.add(entry)
    db.commit()
    return entry


@pytest.mark.asyncio
async def test_semantic_lookup_serves_paraphrase_above_threshold(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    scope = _scope(tenant_id=tenant_row.id, bot_id=None)
    cached = _cached_answer(answer="Cached paraphrase answer")
    _insert_entry(db_session, scope, _unit(1.0, 0.0, 0.0), cached=cached)

    run = _make_run(question="how can i reset the password")
    run.tenant_id = tenant_row.id
    run.db = async_search_session
    paraphrase_scope = _scope(
        tenant_id=tenant_row.id, bot_id=None, question="how can i reset the password"
    )
    run.state.answer_cache_scope = paraphrase_scope
    run.state.variant_vectors = [_unit(1.0, 0.05, 0.0)]  # cosine ≈ 0.9988

    result = await cache_steps.semantic_lookup(run)

    assert result is not None
    assert result.final_answer == "Cached paraphrase answer"
    assert result.answer_cache.level == "semantic"
    assert result.answer_cache.similarity >= settings.answer_cache_semantic_threshold
    assert cache_metrics.snapshot()["answer_semantic"]["hits"] == 1
    # Write-through: the paraphrase now resolves on the exact level.
    assert CachedAnswer.from_payload(fake_redis[paraphrase_scope.exact_key]) == cached


@pytest.mark.asyncio
async def test_semantic_lookup_misses_below_threshold(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    scope = _scope(tenant_id=tenant_row.id, bot_id=None)
    _insert_entry(db_session, scope, _unit(1.0, 0.0, 0.0))

    run = _make_run(question="what are your prices")
    run.tenant_id = tenant_row.id
    run.db = async_search_session
    run.state.answer_cache_scope = _scope(
        tenant_id=tenant_row.id, bot_id=None, question="what are your prices"
    )
    run.state.variant_vectors = [_unit(1.0, 1.0, 0.0)]  # cosine ≈ 0.707

    assert await cache_steps.semantic_lookup(run) is None
    assert cache_metrics.snapshot()["answer_semantic"]["misses"] == 1
    assert fake_redis == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    ["response_language", "kb_fingerprint", "bot_id", "expired"],
)
async def test_semantic_lookup_is_scoped(
    db_session: Session, async_search_session, fake_redis: dict[str, str], mismatch: str
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    stored_scope = _scope(tenant_id=tenant_row.id, bot_id=None)
    expires_in = timedelta(seconds=-1) if mismatch == "expired" else timedelta(hours=1)
    _insert_entry(db_session, stored_scope, _unit(1.0, 0.0, 0.0), expires_in=expires_in)

    lookup_scope = stored_scope
    if mismatch == "response_language":
        lookup_scope = _scope(tenant_id=tenant_row.id, bot_id=None, response_language="de")
    elif mismatch == "kb_fingerprint":
        lookup_scope = _scope(tenant_id=tenant_row.id, bot_id=None, kb_fingerprint="fp-2")
    elif mismatch == "bot_id":
        lookup_scope = _scope(tenant_id=tenant_row.id, bot_id=uuid.uuid4())

    run = _make_run()
    run.tenant_id = tenant_row.id
    run.db = async_search_session
    run.state.answer_cache_scope = lookup_scope
    run.state.variant_vectors = [_unit(1.0, 0.0, 0.0)]  # identical vector

    assert await cache_steps.semantic_lookup(run) is None


@pytest.mark.asyncio
async def test_semantic_lookup_skipped_without_embedding(async_search_session) -> None:
    run = _make_run()
    run.db = async_search_session
    run.state.answer_cache_scope = _scope(tenant_id=run.tenant_id)
    run.state.variant_vectors = []
    assert await cache_steps.semantic_lookup(run) is None
    assert "answer_semantic" not in cache_metrics.snapshot()


@pytest.mark.asyncio
async def test_semantic_lookup_db_error_is_a_miss(
    async_search_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(answer_cache, "_nearest_entry", _boom)
    run = _make_run()
    run.db = async_search_session
    run.state.answer_cache_scope = _scope(tenant_id=run.tenant_id)
    run.state.variant_vectors = [_unit(1.0, 0.0, 0.0)]
    assert await cache_steps.semantic_lookup(run) is None
    assert cache_metrics.snapshot()["answer_semantic"]["misses"] == 1


@pytest.mark.asyncio
async def test_semantic_lookup_cancels_pending_relevance_guard(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    scope = _scope(tenant_id=tenant_row.id, bot_id=None)
    _insert_entry(db_session, scope, _unit(1.0, 0.0, 0.0))

    async def _slow_guard():
        await asyncio.sleep(30)

    run = _make_run()
    run.tenant_id = tenant_row.id
    run.db = async_search_session
    run.state.answer_cache_scope = scope
    run.state.variant_vectors = [_unit(1.0, 0.0, 0.0)]
    run.state.rel_task = asyncio.create_task(_slow_guard())

    result = await cache_steps.semantic_lookup(run)
    assert result is not None
    assert run.state.rel_task.cancelled()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_writes_both_levels_and_purges_stale_rows(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    bots = [Bot(tenant_id=tenant_row.id, name="a"), Bot(tenant_id=tenant_row.id, name="b")]
    db_session.add_all(bots)
    db_session.commit()
    bot_id, other_bot = bots[0].id, bots[1].id
    stale = _insert_entry(
        db_session, _scope(tenant_id=tenant_row.id, bot_id=bot_id, kb_fingerprint="old"), _unit(0, 1, 0)
    )
    expired = _insert_entry(
        db_session,
        _scope(tenant_id=tenant_row.id, bot_id=bot_id, kb_fingerprint="fp-1"),
        _unit(0, 0, 1),
        expires_in=timedelta(seconds=-5),
    )
    other_bot_entry = _insert_entry(
        db_session, _scope(tenant_id=tenant_row.id, bot_id=other_bot, kb_fingerprint="old"), _unit(0, 1, 0)
    )

    stale_id, expired_id, other_bot_entry_id = stale.id, expired.id, other_bot_entry.id

    scope = _scope(tenant_id=tenant_row.id, bot_id=bot_id, kb_fingerprint="fp-1")
    cached = _cached_answer()
    await answer_cache.store(
        AnswerCacheCandidate(scope=scope, question_embedding=_unit(1, 0, 0), answer=cached),
        db=async_search_session,
    )

    assert CachedAnswer.from_payload(fake_redis[scope.exact_key]) == cached
    db_session.expire_all()
    remaining = {row.id for row in db_session.query(AnswerCacheEntry).all()}
    assert stale_id not in remaining
    assert expired_id not in remaining
    assert other_bot_entry_id in remaining  # another bot's rows are untouched
    fresh = (
        db_session.query(AnswerCacheEntry)
        .filter(AnswerCacheEntry.bot_id == bot_id, AnswerCacheEntry.kb_fingerprint == "fp-1")
        .one()
    )
    assert fresh.kb_fingerprint == "fp-1"
    assert fresh.expires_at > _utcnow() + timedelta(seconds=settings.answer_cache_ttl_seconds - 60)


@pytest.mark.asyncio
async def test_store_without_db_only_writes_exact_level(fake_redis: dict[str, str]) -> None:
    scope = _scope()
    await answer_cache.store(
        AnswerCacheCandidate(scope=scope, question_embedding=_unit(1, 0, 0), answer=_cached_answer()),
        db=None,
    )
    assert scope.exact_key in fake_redis


@pytest.mark.asyncio
async def test_store_db_failure_never_raises(
    async_search_session, fake_redis: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(async_search_session, "execute", _boom)
    scope = _scope()
    await answer_cache.store(
        AnswerCacheCandidate(scope=scope, question_embedding=_unit(1, 0, 0), answer=_cached_answer()),
        db=async_search_session,
    )
    assert scope.exact_key in fake_redis


# ---------------------------------------------------------------------------
# Store eligibility (decided by the generation step)
# ---------------------------------------------------------------------------


def _eligible_run() -> PipelineRun:
    run = _make_run()
    run.state.answer_cache_scope = _scope(tenant_id=run.tenant_id)
    run.state.variant_vectors = [_unit(1, 0, 0)]
    run.state.guard_dialog_context = None
    return run


def test_build_store_candidate_for_confident_first_turn() -> None:
    run = _eligible_run()
    result = _generated_result(llm_lang_retry_ms=250)
    candidate = cache_steps.build_store_candidate(run, result, strong_context=True)
    assert candidate is not None
    assert candidate.scope is run.state.answer_cache_scope
    assert candidate.question_embedding == _unit(1, 0, 0)
    assert candidate.answer.answer == "Answer text"
    assert candidate.answer.llm_ms == 4000 + 250
    assert candidate.answer.retrieval_ms == 300
    assert candidate.answer.document_ids == tuple(str(d) for d in result.retrieval.document_ids)


@pytest.mark.parametrize(
    "case",
    [
        "no_scope",
        "dialog_context",
        "no_embedding",
        "weak_context",
        "no_chunks",
        "reliability_cap",
        "escalation",
        "offered_ticket",
        "needs_human",
        "clarifying",
        "clarify_required",
        "empty_answer",
    ],
)
def test_build_store_candidate_rejects_non_reusable_answers(case: str) -> None:
    run = _eligible_run()
    strong_context = True
    result = _generated_result()
    if case == "no_scope":
        run.state.answer_cache_scope = None
    elif case == "dialog_context":
        run.state.guard_dialog_context = "User: hi\nAssistant: hello"
    elif case == "no_embedding":
        run.state.variant_vectors = []
    elif case == "weak_context":
        strong_context = False
    elif case == "no_chunks":
        result = _generated_result(_retrieval(chunk_texts=[], document_ids=[], scores=[]))
    elif case == "reliability_cap":
        capped = build_reliability_assessment(top_score=0.9, result_count=2)
        from dataclasses import replace

        result = _generated_result(
            _retrieval(reliability=replace(capped, cap="medium", cap_reason="source_overlap"))
        )
    elif case == "escalation":
        result = _generated_result(escalation_recommended=True)
    elif case == "offered_ticket":
        result = _generated_result(llm_offered_ticket=True)
    elif case == "needs_human":
        result = _generated_result(llm_needs_human=True)
    elif case == "clarifying":
        result = _generated_result(llm_clarifying=True)
    elif case == "clarify_required":
        result = _generated_result(clarify_required_reason="ambiguous_intent")
    elif case == "empty_answer":
        result = _generated_result(final_answer="   ")
    assert cache_steps.build_store_candidate(run, result, strong_context=strong_context) is None


# ---------------------------------------------------------------------------
# End-to-end through process_chat_message
# ---------------------------------------------------------------------------


def _patch_pipeline_fakes(monkeypatch: pytest.MonkeyPatch, *, answer: str) -> dict[str, int]:
    """Deterministic retrieval + generation so the test isolates the cache."""
    counters = {"generate": 0, "retrieve": 0}

    async def _generate(*_args, **_kwargs):
        counters["generate"] += 1
        return (answer, 40, 30, 10, False, False, False)

    async def _retrieve(*_args, **_kwargs):
        counters["retrieve"] += 1
        return _retrieval(chunk_texts=["Retrieved chunk"])

    async def _no_rewrite(*_args, **_kwargs):
        return None

    monkeypatch.setattr("backend.chat.handlers.rag.async_generate_answer", _generate)
    monkeypatch.setattr("backend.chat.service.async_retrieve_context", _retrieve)
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))
    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(
            lambda **_: FAQMatchResult(
                strategy="rag_only",
                faq_items=[],
                top_score=None,
                selected_score=None,
                selected_faq_id=None,
                direct_guard_used=False,
                direct_guard_passed=False,
                decision_reason="test",
            )
        ),
    )
    monkeypatch.setattr("backend.chat.service._start_mode_b_followup", lambda _tenant_id: None)
    monkeypatch.setattr("backend.chat.service.async_semantic_query_rewrite", _no_rewrite)
    monkeypatch.setattr("backend.chat.service.async_semantic_query_rewrite_for_kb", _no_rewrite)
    return counters


def _turn_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    captured: list[dict] = []

    def _capture(event: str, **kwargs):
        if event == "chat.turn":
            captured.append(kwargs["properties"])

    monkeypatch.setattr("backend.chat.events.capture_event", _capture)
    return captured


def test_repeated_question_is_served_from_cache_without_openai(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-hit@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Use the reset link in Settings.")
    turn_events = _turn_events(monkeypatch)

    first = process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert first.text == "Use the reset link in Settings."
    assert counters == {"generate": 1, "retrieve": 1}
    assert db_session.query(AnswerCacheEntry).count() == 1
    embed_calls_after_first = mock_openai_client.embeddings.create.call_count
    assert turn_events[-1]["answer_cache_hit"] is False

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("injection guard must not run on an exact cache hit")

    monkeypatch.setattr("backend.chat.service.async_detect_injection", _must_not_run)

    second = process_chat_message(cl_row.id, "  how do I reset my PASSWORD? ", uuid.uuid4(), db_session, api_key=api_key)

    assert second.text == first.text
    assert second.document_ids == first.document_ids
    assert counters == {"generate": 1, "retrieve": 1}
    assert mock_openai_client.embeddings.create.call_count == embed_calls_after_first
    assert db_session.query(AnswerCacheEntry).count() == 1  # a hit is not re-stored
    assert turn_events[-1]["answer_cache_hit"] is True
    assert turn_events[-1]["answer_cache_level"] == "exact"
    assert turn_events[-1]["answer_cache_saved_ms"] >= 0
    assert cache_metrics.snapshot()["answer_exact"] == {"hits": 1, "misses": 1, "hit_rate": 0.5}


def test_cache_is_not_consulted_after_knowledge_base_change(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-kb@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Old answer")

    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert counters["generate"] == 1

    _insert_single_chunk(db_session, tenant_id=cl_row.id, chunk_text="New doc")

    outcome = process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert counters["generate"] == 2
    assert outcome.text == "Old answer"  # regenerated (fake), not served from cache


def test_cache_is_not_consulted_after_bot_instructions_change(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    from backend.models import Bot

    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-bot@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")
    bot = db_session.query(Bot).filter(Bot.tenant_id == cl_row.id).first()
    assert bot is not None

    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key, bot_id=bot.id)
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key, bot_id=bot.id)
    assert counters["generate"] == 1

    bot.agent_instructions = "Answer only in bullet points."
    db_session.commit()

    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key, bot_id=bot.id)
    assert counters["generate"] == 2


def test_language_switch_does_not_serve_cached_answer(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-lang@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")

    def _resolve(language: str):
        def _inner(**_kwargs):
            return _language_context(language)

        return _inner

    monkeypatch.setattr("backend.chat.service._resolve_chat_language_context", _resolve("en"))
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert counters["generate"] == 1

    # Same wording, different resolved response language → separate key.
    monkeypatch.setattr("backend.chat.service._resolve_chat_language_context", _resolve("de"))
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert counters["generate"] == 2


def test_user_context_turns_bypass_the_cache(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-ctx@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")
    identified = {"name": "Alice", "plan_tier": "pro"}

    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key, user_context=identified)
    assert fake_redis == {}
    assert db_session.query(AnswerCacheEntry).count() == 0

    # A cached anonymous answer is never served to an identified visitor.
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert db_session.query(AnswerCacheEntry).count() == 1
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key, user_context=identified)
    assert counters["generate"] == 3


def test_pii_redacted_questions_bypass_the_cache(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-pii@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")

    question = "Reset the password for john.doe@example.com please"
    process_chat_message(cl_row.id, question, uuid.uuid4(), db_session, api_key=api_key)
    process_chat_message(cl_row.id, question, uuid.uuid4(), db_session, api_key=api_key)
    assert counters["generate"] == 2
    assert fake_redis == {}
    assert db_session.query(AnswerCacheEntry).count() == 0


def test_follow_up_turns_are_not_stored(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-followup@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    _patch_pipeline_fakes(monkeypatch, answer="Answer")
    session_id = uuid.uuid4()

    process_chat_message(cl_row.id, "How do I reset my password?", session_id, db_session, api_key=api_key)
    assert db_session.query(AnswerCacheEntry).count() == 1
    process_chat_message(cl_row.id, "And how do I change it afterwards?", session_id, db_session, api_key=api_key)
    assert db_session.query(AnswerCacheEntry).count() == 1  # second turn had dialog history


def test_escalation_state_turns_never_touch_the_cache(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-esc@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    _patch_pipeline_fakes(monkeypatch, answer="Answer")

    # Warm the cache with the exact question the pre-confirm turn will repeat.
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert len(fake_redis) == 1

    chat = Chat(
        tenant_id=cl_row.id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_pre_confirm_pending=True,
        escalation_pre_confirm_context={"trigger": "low_similarity", "primary_question": "x"},
    )
    db_session.add(chat)
    db_session.commit()

    lookups: list[str] = []
    original_get = redis_mod.cache_get

    async def _spy(key: str):
        lookups.append(key)
        return await original_get(key)

    monkeypatch.setattr(redis_mod, "cache_get", _spy)
    monkeypatch.setattr(
        "backend.chat.service.classify_pre_confirm_reply",
        _as_async(lambda **_: ("unclear", 0)),
    )

    outcome = process_chat_message(cl_row.id, "How do I reset my password?", chat.session_id, db_session, api_key=api_key)
    assert outcome.text != "Answer"
    assert not any(key.startswith("cache:answer:") for key in lookups)


def test_escalated_reply_is_not_stored(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-escalate@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    _patch_pipeline_fakes(monkeypatch, answer="Weak answer")
    from backend.models import EscalationTrigger

    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *_, **__: (True, EscalationTrigger.low_similarity),
    )
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert fake_redis == {}
    assert db_session.query(AnswerCacheEntry).count() == 0


def test_redis_unavailable_keeps_the_pipeline_working(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-redis-down@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")

    async def _down_get(key: str):
        return None

    async def _down_set(key: str, value: str, ttl: int) -> bool:
        return False

    monkeypatch.setattr(redis_mod, "is_enabled", lambda: True)
    monkeypatch.setattr(redis_mod, "cache_get", _down_get)
    monkeypatch.setattr(redis_mod, "cache_set_with_ttl", _down_set)

    first = process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert first.text == "Answer"
    # The semantic level still catches the identical question on its own.
    second = process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert second.text == "Answer"
    assert counters["generate"] == 1
    assert cache_metrics.snapshot()["answer_semantic"]["hits"] == 1


def test_hit_result_preserves_trace_metadata(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    from tests.test_rag_pipeline import _FakeTrace

    class _Trace(_FakeTrace):
        def __init__(self) -> None:
            super().__init__()
            self.updates: list[dict] = []

        def update(self, **kwargs) -> None:
            self.updates.append(kwargs)

    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-trace@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    _patch_pipeline_fakes(monkeypatch, answer="Answer")
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)

    trace = _Trace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: trace)
    process_chat_message(cl_row.id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)

    final = [u for u in trace.updates if u.get("metadata", {}).get("answer_cache_hit") is not None][-1]
    assert final["metadata"]["answer_cache_hit"] is True
    assert final["metadata"]["answer_cache_level"] == "exact"
    assert "answer_cache_hit" in final["tags"]
    assert "answer-cache" in trace.spans


def test_hit_answer_shape() -> None:
    hit = AnswerCacheHit(level="semantic", similarity=0.97, answer=_cached_answer())
    assert hit.saved_ms == 4540
