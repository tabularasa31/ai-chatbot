"""Answer cache: exact level (Redis) + semantic level (pgvector) in front of
retrieval and generation. See backend/chat/answer_cache.py."""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat import answer_cache
from backend.chat.answer_cache import (
    AnswerCacheCandidate,
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
    EscalationTrigger,
    Tenant,
)
from backend.models.base import _utcnow
from backend.observability import cache_metrics
from backend.search.service import build_reliability_assessment
from tests._async_utils import as_async as _as_async
from tests.test_rag_pipeline import _create_client, _FakeTrace, _insert_single_chunk


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
        bot_id=None,
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


def _make_run(tenant_id: uuid.UUID | None = None, db=None) -> PipelineRun:
    return PipelineRun(
        tenant_id=tenant_id or uuid.uuid4(),
        question="How do I reset my password?",
        db=db,
        api_key="test-key",
        language_context=_language_context(),
        answer_cache_eligible=True,
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


def _unit(x: float, y: float, z: float) -> list[float]:
    """Unit vector in the first three of the model's 1536 dimensions."""
    norm = (x * x + y * y + z * z) ** 0.5
    return [x / norm, y / norm, z / norm] + [0.0] * 1533


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


def _insert_entry(
    db: Session,
    scope: AnswerCacheScope,
    embedding: list[float],
    *,
    cached: CachedAnswer | None = None,
    expires_in: timedelta = timedelta(hours=1),
) -> uuid.UUID:
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
    return entry.id


# ---------------------------------------------------------------------------
# Keys and payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  How do I   reset my password?  ", "how do i reset my password"),
        ("¿Cómo restablezco mi contraseña?", "cómo restablezco mi contraseña"),
        ("Как сбросить пароль?!", "как сбросить пароль"),
    ],
)
def test_normalize_question_is_script_agnostic(raw: str, expected: str) -> None:
    assert normalize_question(raw) == expected


def test_cached_answer_payload_round_trip_and_validation() -> None:
    cached = _cached_answer()
    assert CachedAnswer.from_payload(cached.to_json()) == cached
    assert CachedAnswer.from_payload(cached.to_dict()).saved_ms == 340 + 4200
    assert CachedAnswer.from_payload("not json") is None
    assert CachedAnswer.from_payload({"v": 99, "answer": "x", "strategy": "rag_only"}) is None
    assert (
        CachedAnswer.from_payload(
            {"v": 1, "answer": "x", "strategy": "rag_only", "document_ids": ["nope"]}
        )
        is None
    )


@pytest.mark.asyncio
async def test_fingerprint_tracks_documents_bot_config_and_reindex(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, doc = _tenant_with_doc(db_session)

    async def resolve(**overrides) -> AnswerCacheScope:
        kwargs = dict(
            tenant_id=tenant_row.id,
            bot_id=None,
            response_language="en",
            question="How do I reset my password?",
            agent_instructions="Be brief.",
            disclosure_config=None,
            db=async_search_session,
        )
        kwargs.update(overrides)
        return await answer_cache.resolve_scope(**kwargs)

    seen = {(await resolve()).kb_fingerprint}
    assert (await resolve()).kb_fingerprint in seen  # stable while nothing changed

    for change in (
        lambda: resolve(agent_instructions="Be verbose."),
        lambda: resolve(disclosure_config={"level": "strict"}),
    ):
        fingerprint = (await change()).kb_fingerprint
        assert fingerprint not in seen
        seen.add(fingerprint)

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
    after_upload = (await resolve()).kb_fingerprint
    assert after_upload not in seen
    seen.add(after_upload)

    db_session.delete(doc)
    db_session.commit()
    after_delete = (await resolve()).kb_fingerprint
    assert after_delete not in seen
    seen.add(after_delete)

    await answer_cache.invalidate_tenant(tenant_row.id)  # what a re-index does
    assert (await resolve()).kb_fingerprint not in seen


# ---------------------------------------------------------------------------
# Lookup steps
# ---------------------------------------------------------------------------


def test_exact_lookup_serves_cached_answer(fake_redis: dict[str, str]) -> None:
    run = _make_run()
    scope = _scope(tenant_id=run.tenant_id)
    run.state.answer_cache_scope = scope
    cached = _cached_answer()
    fake_redis[scope.exact_key] = cached.to_json()

    result = asyncio.run(cache_steps.exact_lookup(run))

    assert result is not None
    assert result.final_answer == cached.answer
    assert result.tokens_used == 0 and result.llm_ms == 0 and result.retrieval_ms == 0
    assert result.answer_cache.level == "exact"
    assert result.answer_cache.saved_ms == cached.saved_ms
    assert [str(d) for d in result.retrieval.document_ids] == list(cached.document_ids)
    assert result.retrieval.reliability.score == "high"
    assert result.escalation_recommended is False
    assert cache_metrics.snapshot()["answer_exact"]["hits"] == 1


@pytest.mark.asyncio
async def test_semantic_lookup_serves_paraphrase_and_promotes_to_exact(
    db_session: Session, async_search_session, fake_redis: dict[str, str]
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    stored = _scope(tenant_id=tenant_row.id)
    cached = _cached_answer(answer="Cached paraphrase answer")
    _insert_entry(db_session, stored, _unit(1.0, 0.0, 0.0), cached=cached)

    async def _slow_guard():
        await asyncio.sleep(30)

    run = _make_run(tenant_row.id, async_search_session)
    lookup = _scope(tenant_id=tenant_row.id, question="how can i reset the password")
    run.state.answer_cache_scope = lookup
    run.state.variant_vectors = [_unit(1.0, 0.05, 0.0)]  # cosine ≈ 0.9988
    run.state.rel_task = asyncio.create_task(_slow_guard())

    result = await cache_steps.semantic_lookup(run)

    assert result is not None
    assert result.final_answer == "Cached paraphrase answer"
    assert result.answer_cache.level == "semantic"
    assert result.answer_cache.similarity >= settings.answer_cache_semantic_threshold
    assert run.state.rel_task.cancelled()
    assert CachedAnswer.from_payload(fake_redis[lookup.exact_key]) == cached


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    ["below_threshold", "response_language", "kb_fingerprint", "bot_id", "expired"],
)
async def test_semantic_lookup_misses_outside_scope(
    db_session: Session, async_search_session, fake_redis: dict[str, str], mismatch: str
) -> None:
    tenant_row, _ = _tenant_with_doc(db_session)
    stored = _scope(tenant_id=tenant_row.id)
    expires_in = timedelta(seconds=-1) if mismatch == "expired" else timedelta(hours=1)
    _insert_entry(db_session, stored, _unit(1.0, 0.0, 0.0), expires_in=expires_in)

    lookup = stored
    vector = _unit(1.0, 0.0, 0.0)
    if mismatch == "below_threshold":
        vector = _unit(1.0, 1.0, 0.0)  # cosine ≈ 0.707
    elif mismatch == "response_language":
        lookup = replace(stored, response_language="de")
    elif mismatch == "kb_fingerprint":
        lookup = replace(stored, kb_fingerprint="fp-2")
    elif mismatch == "bot_id":
        lookup = replace(stored, bot_id=uuid.uuid4())

    run = _make_run(tenant_row.id, async_search_session)
    run.state.answer_cache_scope = lookup
    run.state.variant_vectors = [vector]

    assert await cache_steps.semantic_lookup(run) is None
    assert cache_metrics.snapshot()["answer_semantic"]["misses"] == 1
    assert fake_redis == {}


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
        _scope(tenant_id=tenant_row.id, bot_id=bot_id),
        _unit(0, 0, 1),
        expires_in=timedelta(seconds=-5),
    )
    other = _insert_entry(
        db_session, _scope(tenant_id=tenant_row.id, bot_id=other_bot, kb_fingerprint="old"), _unit(0, 1, 0)
    )

    scope = _scope(tenant_id=tenant_row.id, bot_id=bot_id)
    cached = _cached_answer()
    await answer_cache.store(
        AnswerCacheCandidate(scope=scope, question_embedding=_unit(1, 0, 0), answer=cached),
        db=async_search_session,
    )

    assert CachedAnswer.from_payload(fake_redis[scope.exact_key]) == cached
    db_session.expire_all()
    remaining = {row.id for row in db_session.query(AnswerCacheEntry).all()}
    assert stale not in remaining and expired not in remaining
    assert other in remaining  # another bot's rows are untouched
    fresh = (
        db_session.query(AnswerCacheEntry)
        .filter(AnswerCacheEntry.bot_id == bot_id, AnswerCacheEntry.kb_fingerprint == "fp-1")
        .one()
    )
    assert fresh.expires_at > _utcnow() + timedelta(seconds=settings.answer_cache_ttl_seconds - 60)


def test_build_store_candidate_only_for_self_contained_confident_answers() -> None:
    def eligible_run() -> PipelineRun:
        run = _make_run()
        run.state.answer_cache_scope = _scope(tenant_id=run.tenant_id)
        run.state.variant_vectors = [_unit(1, 0, 0)]
        return run

    run = eligible_run()
    candidate = cache_steps.build_store_candidate(
        run, _generated_result(llm_lang_retry_ms=250), strong_context=True
    )
    assert candidate is not None
    assert candidate.scope is run.state.answer_cache_scope
    assert candidate.answer.answer == "Answer text"
    assert candidate.answer.llm_ms == 4000 + 250

    follow_up = eligible_run()
    follow_up.state.guard_dialog_context = "User: hi\nAssistant: hello"
    assert cache_steps.build_store_candidate(follow_up, _generated_result(), strong_context=True) is None

    assert cache_steps.build_store_candidate(eligible_run(), _generated_result(), strong_context=False) is None

    capped = replace(
        build_reliability_assessment(top_score=0.9, result_count=2),
        cap="medium",
        cap_reason="source_overlap",
    )
    rejected = [
        _generated_result(_retrieval(reliability=capped)),
        _generated_result(escalation_recommended=True),
        _generated_result(llm_offered_ticket=True),
        _generated_result(llm_needs_human=True),
        _generated_result(llm_clarifying=True),
        _generated_result(clarify_required_reason="ambiguous_intent"),
    ]
    for result in rejected:
        assert cache_steps.build_store_candidate(eligible_run(), result, strong_context=True) is None


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

    async def _embed(texts: list[str], **_kwargs) -> list[list[float]]:
        # Text-dependent vectors: the shared mock returns one constant vector
        # for every input, which would make every question a semantic hit.
        vectors = []
        for text in texts:
            rng = random.Random(normalize_question(text))
            raw = [rng.gauss(0.0, 1.0) for _ in range(1536)]
            norm = sum(v * v for v in raw) ** 0.5
            vectors.append([v / norm for v in raw])
        return vectors

    monkeypatch.setattr("backend.chat.service.async_embed_queries", _embed)
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


def _ask(cl_row: Tenant, api_key: str, db: Session, question: str = "How do I reset my password?", **kwargs):
    session_id = kwargs.pop("session_id", None) or uuid.uuid4()
    return process_chat_message(cl_row.id, question, session_id, db, api_key=api_key, **kwargs)


def test_repeated_question_is_served_from_cache_without_openai(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    class _Trace(_FakeTrace):
        def __init__(self) -> None:
            super().__init__()
            self.updates: list[dict] = []

        def update(self, **kwargs) -> None:
            self.updates.append(kwargs)

    turn_events: list[dict] = []

    def _capture(event: str, **kwargs):
        if event == "chat.turn":
            turn_events.append(kwargs["properties"])

    monkeypatch.setattr("backend.chat.events.capture_event", _capture)
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-hit@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Use the reset link in Settings.")

    first = _ask(cl_row, api_key, db_session)
    assert first.text == "Use the reset link in Settings."
    assert counters == {"generate": 1, "retrieve": 1}
    assert db_session.query(AnswerCacheEntry).count() == 1
    assert turn_events[-1]["answer_cache_hit"] is False
    embed_calls = mock_openai_client.embeddings.create.call_count

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("injection guard must not run on an exact cache hit")

    monkeypatch.setattr("backend.chat.service.async_detect_injection", _must_not_run)
    trace = _Trace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: trace)

    second = _ask(cl_row, api_key, db_session, "  how do I reset my PASSWORD? ")

    assert second.text == first.text
    assert second.document_ids == first.document_ids
    assert counters == {"generate": 1, "retrieve": 1}
    assert mock_openai_client.embeddings.create.call_count == embed_calls
    assert db_session.query(AnswerCacheEntry).count() == 1  # a hit is not re-stored
    assert turn_events[-1]["answer_cache_hit"] is True
    assert turn_events[-1]["answer_cache_level"] == "exact"
    assert cache_metrics.snapshot()["answer_exact"] == {"hits": 1, "misses": 1, "hit_rate": 0.5}
    final = [u for u in trace.updates if u.get("metadata", {}).get("answer_cache_hit") is not None][-1]
    assert final["metadata"]["answer_cache_level"] == "exact"
    assert "answer_cache_hit" in final["tags"]
    assert "answer-cache" in trace.spans


def test_knowledge_base_or_bot_change_invalidates_cached_answer(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-kb@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")
    bot = db_session.query(Bot).filter(Bot.tenant_id == cl_row.id).first()

    _ask(cl_row, api_key, db_session, bot_id=bot.id)
    _ask(cl_row, api_key, db_session, bot_id=bot.id)
    assert counters["generate"] == 1

    _insert_single_chunk(db_session, tenant_id=cl_row.id, chunk_text="New doc")
    _ask(cl_row, api_key, db_session, bot_id=bot.id)
    assert counters["generate"] == 2

    bot.agent_instructions = "Answer only in bullet points."
    db_session.commit()
    _ask(cl_row, api_key, db_session, bot_id=bot.id)
    assert counters["generate"] == 3


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
        return lambda **_kwargs: _language_context(language)

    monkeypatch.setattr("backend.chat.service._resolve_chat_language_context", _resolve("en"))
    _ask(cl_row, api_key, db_session)
    _ask(cl_row, api_key, db_session)
    assert counters["generate"] == 1

    # Same wording, different resolved response language → separate key.
    monkeypatch.setattr("backend.chat.service._resolve_chat_language_context", _resolve("de"))
    _ask(cl_row, api_key, db_session)
    assert counters["generate"] == 2


def test_personal_and_session_dependent_turns_bypass_the_cache(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: dict[str, str],
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="answer-cache-bypass@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)
    counters = _patch_pipeline_fakes(monkeypatch, answer="Answer")
    identified = {"name": "Alice", "plan_tier": "pro"}

    # Identified visitor and PII-redacted question: nothing stored.
    _ask(cl_row, api_key, db_session, user_context=identified)
    _ask(cl_row, api_key, db_session, "Reset the password for john.doe@example.com please")
    assert fake_redis == {}
    assert db_session.query(AnswerCacheEntry).count() == 0

    # Anonymous first turn is cached; the identified visitor still gets a fresh answer.
    _ask(cl_row, api_key, db_session)
    assert db_session.query(AnswerCacheEntry).count() == 1
    _ask(cl_row, api_key, db_session, user_context=identified)
    assert counters["generate"] == 4

    # A follow-up in an existing chat is never stored.
    session_id = uuid.uuid4()
    _ask(cl_row, api_key, db_session, "Where can I find the notification settings page?", session_id=session_id)
    assert db_session.query(AnswerCacheEntry).count() == 2
    _ask(cl_row, api_key, db_session, "And how do I change them afterwards?", session_id=session_id)
    assert db_session.query(AnswerCacheEntry).count() == 2

    # An escalating reply is never stored.
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *_, **__: (True, EscalationTrigger.low_similarity),
    )
    _ask(cl_row, api_key, db_session, "Do you support SAML?")
    assert db_session.query(AnswerCacheEntry).count() == 2

    # A chat waiting on the pre-confirm gate never consults the cache.
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
        "backend.chat.service.classify_pre_confirm_reply", _as_async(lambda **_: ("unclear", 0))
    )
    outcome = _ask(cl_row, api_key, db_session, session_id=chat.session_id)
    assert outcome.text != "Answer"
    assert not any(key.startswith("cache:answer:") for key in lookups)


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

    assert _ask(cl_row, api_key, db_session).text == "Answer"
    # The semantic level still catches the identical question on its own.
    assert _ask(cl_row, api_key, db_session).text == "Answer"
    assert counters["generate"] == 1
    assert cache_metrics.snapshot()["answer_semantic"]["hits"] == 1
