"""Reranker strategies: selection, blending, timeout and fallback contract."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from backend.models import Embedding, RerankerStrategy
from backend.search import reranking
from backend.search.reranking import (
    HEURISTIC_MODEL_NAME,
    CrossEncoderReranker,
    LLMReranker,
    RerankerUnavailableError,
    RerankSignals,
    _parse_llm_scores,
    rerank_candidates,
    rerank_with_fallback,
)


def _embedding(text: str, index: int, document_id: uuid.UUID | None = None) -> Embedding:
    return Embedding(
        id=uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        chunk_text=text,
        metadata_json={"chunk_index": index},
    )


def _pool() -> tuple[list[tuple[Embedding, float]], RerankSignals]:
    """Three candidates where the heuristic favours lexical overlap on 'reset password'."""
    lexical = _embedding("reset password from the dashboard settings page", 0)
    generic = _embedding("password rules: minimum length and complexity", 1)
    specific = _embedding("if the reset link expired, request a new one from login", 2)
    candidates = [(lexical, 0.9), (generic, 0.8), (specific, 0.7)]
    signals = RerankSignals(
        vector_scores={lexical.id: 0.8, generic.id: 0.7, specific.id: 0.6},
        bm25_scores={lexical.id: 1.0, generic.id: 0.5, specific.id: 0.2},
    )
    return candidates, signals


@pytest.mark.asyncio
async def test_heuristic_strategy_is_the_baseline() -> None:
    candidates, signals = _pool()
    outcome = await rerank_with_fallback(
        "reset password",
        candidates,
        strategy=RerankerStrategy.heuristic.value,
        signals=signals,
        top_k=2,
        api_key="sk-test",
    )
    expected = rerank_candidates(
        "reset password",
        candidates,
        vector_scores=signals.vector_scores,
        bm25_scores=signals.bm25_scores,
        top_k=2,
    )
    assert outcome.results == expected
    assert outcome.strategy_applied == "heuristic"
    assert outcome.model == HEURISTIC_MODEL_NAME
    assert outcome.fallback_reason is None


@pytest.mark.asyncio
async def test_llm_strategy_reorders_by_semantic_score(monkeypatch) -> None:
    candidates, signals = _pool()
    specific = candidates[2][0]
    seen: dict[str, object] = {}

    async def fake_score(self, query: str, passages: list[str]) -> list[float]:
        seen["query"] = query
        seen["passages"] = passages
        return [1.0 if "expired" in passage else 0.2 for passage in passages]

    monkeypatch.setattr(LLMReranker, "score", fake_score)
    outcome = await rerank_with_fallback(
        "my reset link expired",
        candidates,
        strategy=RerankerStrategy.llm.value,
        signals=signals,
        top_k=2,
        api_key="sk-test",
    )

    assert outcome.strategy_applied == "llm"
    assert outcome.fallback_reason is None
    assert outcome.results[0][0].id == specific.id
    assert len(outcome.results) == 2
    assert seen["query"] == "my reset link expired"
    assert len(seen["passages"]) == 3
    # Semantic score dominates but the heuristic still contributes to the blend.
    assert 0.8 < outcome.results[0][1] <= 1.0


@pytest.mark.asyncio
async def test_llm_timeout_falls_back_to_heuristic(monkeypatch) -> None:
    candidates, signals = _pool()

    async def slow_score(self, query: str, passages: list[str]) -> list[float]:
        await asyncio.sleep(5)
        return [1.0] * len(passages)

    monkeypatch.setattr(LLMReranker, "score", slow_score)
    monkeypatch.setattr(reranking.settings, "reranker_timeout_seconds", 0.05)
    outcome = await rerank_with_fallback(
        "reset password",
        candidates,
        strategy="llm",
        signals=signals,
        top_k=2,
        api_key="sk-test",
    )

    assert outcome.strategy_requested == "llm"
    assert outcome.strategy_applied == "heuristic"
    assert outcome.fallback_reason == "timeout"
    assert outcome.model == HEURISTIC_MODEL_NAME
    assert outcome.results == rerank_candidates(
        "reset password",
        candidates,
        vector_scores=signals.vector_scores,
        bm25_scores=signals.bm25_scores,
        top_k=2,
    )


@pytest.mark.asyncio
async def test_llm_error_falls_back_to_heuristic(monkeypatch) -> None:
    candidates, signals = _pool()

    async def broken_score(self, query: str, passages: list[str]) -> list[float]:
        raise ValueError("bad json")

    monkeypatch.setattr(LLMReranker, "score", broken_score)
    outcome = await rerank_with_fallback(
        "reset password",
        candidates,
        strategy="llm",
        signals=signals,
        top_k=2,
        api_key="sk-test",
    )
    assert outcome.strategy_applied == "heuristic"
    assert outcome.fallback_reason == "error: ValueError"
    assert outcome.results


@pytest.mark.asyncio
async def test_llm_without_tenant_key_is_unavailable() -> None:
    candidates, signals = _pool()
    outcome = await rerank_with_fallback(
        "reset password",
        candidates,
        strategy="llm",
        signals=signals,
        top_k=2,
        api_key=None,
    )
    assert outcome.strategy_applied == "heuristic"
    assert outcome.fallback_reason is not None
    assert outcome.fallback_reason.startswith("unavailable:")


@pytest.mark.asyncio
async def test_llm_reranker_parses_openai_json_scores(monkeypatch) -> None:
    candidates, signals = _pool()
    requests: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            scores = {
                index: 9 if "expired" in passage else 2
                for index, passage in re.findall(
                    r"\[(\d+)\] (.*)", kwargs["messages"][1]["content"]
                )
            }
            content = json.dumps({"scores": scores})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(reranking, "get_async_openai_client", lambda *a, **k: fake_client)

    outcome = await rerank_with_fallback(
        "my reset link expired",
        candidates,
        strategy="llm",
        signals=signals,
        top_k=3,
        api_key="sk-test",
    )

    assert outcome.strategy_applied == "llm"
    assert outcome.results[0][0].id == candidates[2][0].id
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "[3]" in requests[0]["messages"][1]["content"]


def test_parse_llm_scores_clamps_and_defaults_missing() -> None:
    raw = json.dumps({"scores": {"1": 12, "2": "x", "4": -3}})
    assert _parse_llm_scores(raw, 4) == [1.0, 0.0, 0.0, 0.0]
    with pytest.raises(ValueError):
        _parse_llm_scores(json.dumps({"ranked": [1, 2]}), 2)


@pytest.mark.asyncio
async def test_cross_encoder_scores_with_loaded_model(monkeypatch) -> None:
    candidates, signals = _pool()

    class FakeModel:
        def predict(self, pairs):
            # Raw logits, as the loader forces an identity activation.
            return [6.0 if "expired" in passage else -6.0 for _, passage in pairs]

    monkeypatch.setattr(reranking, "_load_cross_encoder", lambda name: FakeModel())
    outcome = await rerank_with_fallback(
        "my reset link expired",
        candidates,
        strategy="cross_encoder",
        signals=signals,
        top_k=3,
        api_key=None,
    )
    assert outcome.strategy_applied == "cross_encoder"
    assert outcome.model == CrossEncoderReranker().model
    assert outcome.results[0][0].id == candidates[2][0].id
    # Sigmoid applied exactly once: strong logits map near the ends of the 0-1 scale.
    assert outcome.results[0][1] > 0.8
    assert outcome.results[-1][1] < 0.3


@pytest.mark.asyncio
async def test_cross_encoder_cold_load_does_not_block_other_turns(monkeypatch) -> None:
    candidates, signals = _pool()
    reranking._cross_encoder_lock.acquire()
    try:
        outcome = await rerank_with_fallback(
            "reset password",
            candidates,
            strategy="cross_encoder",
            signals=signals,
            top_k=2,
            api_key=None,
        )
    finally:
        reranking._cross_encoder_lock.release()
    assert outcome.strategy_applied == "heuristic"
    assert outcome.fallback_reason == "unavailable: cross-encoder is still loading"


@pytest.mark.asyncio
async def test_cross_encoder_missing_dependency_falls_back(monkeypatch) -> None:
    candidates, signals = _pool()

    def missing(name: str):
        raise RerankerUnavailableError("sentence-transformers is not installed")

    monkeypatch.setattr(reranking, "_load_cross_encoder", missing)
    outcome = await rerank_with_fallback(
        "reset password",
        candidates,
        strategy="cross_encoder",
        signals=signals,
        top_k=2,
        api_key=None,
    )
    assert outcome.strategy_applied == "heuristic"
    assert outcome.fallback_reason == "unavailable: sentence-transformers is not installed"


@pytest.mark.asyncio
async def test_semantic_strategy_skips_scoring_on_empty_pool(monkeypatch) -> None:
    called = False

    async def fake_score(self, query, passages):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(LLMReranker, "score", fake_score)
    outcome = await rerank_with_fallback(
        "anything",
        [],
        strategy="llm",
        signals=RerankSignals(vector_scores={}, bm25_scores={}),
        top_k=3,
        api_key="sk-test",
    )
    assert outcome.results == []
    assert called is False


def test_tenant_defaults_to_heuristic(db_session: Session) -> None:
    from tests.test_models import _create_client, _create_user

    user = _create_user(db_session, email="reranker-default@example.com")
    tenant = _create_client(db_session, user, name="Reranker Default")
    db_session.refresh(tenant)
    assert tenant.reranker_strategy == RerankerStrategy.heuristic


@pytest.mark.asyncio
async def test_pipeline_reads_tenant_strategy_and_traces_it(
    monkeypatch, db_session: Session, async_search_session
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType
    from backend.search.service import search_similar_chunks_detailed_async
    from backend.tenants.cache import clear_cache
    from tests.test_models import _create_client, _create_user

    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input: dict[str, object] | None = None
            self.output: dict[str, object] | None = None

        def end(self, **kwargs: object) -> None:
            self.output = kwargs["output"]

    class FakeTrace:
        def __init__(self) -> None:
            self.spans: list[FakeSpan] = []

        def span(self, **kwargs: object) -> FakeSpan:
            span = FakeSpan(kwargs["name"])
            span.input = kwargs["input"]
            self.spans.append(span)
            return span

    clear_cache()
    user = _create_user(db_session, email="reranker-pipeline@example.com")
    tenant = _create_client(db_session, user, name="Reranker Pipeline")
    tenant.reranker_strategy = RerankerStrategy.llm
    doc = Document(
        tenant_id=tenant.id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.add_all(
        [
            Embedding(
                document_id=doc.id,
                chunk_text="reset password from the dashboard settings page",
                vector=None,
                metadata_json={"chunk_index": 0, "vector": [1.0, 0.0, 0.0]},
            ),
            Embedding(
                document_id=doc.id,
                chunk_text="if the reset link expired request a new one from login",
                vector=None,
                metadata_json={"chunk_index": 1, "vector": [0.9, 0.1, 0.0]},
            ),
        ]
    )
    db_session.commit()

    async def fake_embed_queries(queries, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in queries]

    monkeypatch.setattr("backend.search.embedding.async_embed_queries", fake_embed_queries)

    async def fake_score(self, query, passages):
        return [0.1 if "settings page" in p else 1.0 for p in passages]

    monkeypatch.setattr(LLMReranker, "score", fake_score)

    trace = FakeTrace()
    bundle = await search_similar_chunks_detailed_async(
        tenant_id=tenant.id,
        query="my reset link expired",
        top_k=2,
        db=async_search_session,
        api_key="sk-test",
        trace=trace,
    )

    span = next(s for s in trace.spans if s.name == "reranking")
    assert span.input is not None and span.input["strategy"] == "llm"
    assert span.output is not None
    assert span.output["strategy_applied"] == "llm"
    assert span.output["fallback_reason"] is None
    assert bundle.results[0][0].chunk_text.startswith("if the reset link expired")


def test_owner_sets_reranker_strategy_via_tenant_api(tenant, db_session: Session) -> None:
    from backend.models import Tenant
    from tests.conftest import register_and_verify_user

    token = register_and_verify_user(tenant, db_session, email="reranker-owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = tenant.post("/tenants", headers=headers, json={"name": "Reranker Co"})
    assert created.status_code in (200, 201), created.text
    assert created.json()["reranker_strategy"] == "heuristic"

    updated = tenant.patch("/tenants/me", headers=headers, json={"reranker_strategy": "llm"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["reranker_strategy"] == "llm"
    row = db_session.query(Tenant).filter(Tenant.id == uuid.UUID(created.json()["id"])).one()
    db_session.refresh(row)
    assert row.reranker_strategy == RerankerStrategy.llm

    rejected = tenant.patch("/tenants/me", headers=headers, json={"reranker_strategy": "bm25"})
    assert rejected.status_code == 422
