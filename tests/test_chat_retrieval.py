"""Unit tests for retrieve_context."""

from __future__ import annotations

import uuid

import pytest

from backend.chat.service import retrieve_context


def test_retrieve_context_propagates_reliability_cap_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import SearchResultBundle, build_reliability_assessment

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.chat.service.search_similar_chunks_detailed",
        lambda *args, **kwargs: SearchResultBundle(
            results=[(embedding, 0.88)],
            best_vector_similarity=0.88,
            query_variants=["reset password"],
            reliability=build_reliability_assessment(
                top_score=0.88,
                result_count=5,
                source_overlap_detected=True,
            ),
        ),
    )

    class FakeBind:
        url = "postgresql://test"

    class FakeDB:
        bind = FakeBind()

    context = retrieve_context(
        tenant_id=uuid.uuid4(),
        question="reset password",
        db=FakeDB(),
        api_key="sk-test",
    )

    assert context.reliability.source_overlap_detected is True
    assert context.reliability.source_overlap_pairs == []
    assert context.reliability.score == "medium"
    assert context.reliability.cap_reason == "source_overlap"


def test_retrieve_context_uses_vector_confidence_and_lexical_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import SearchResultBundle

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="secret number explanation",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.chat.service.search_similar_chunks_detailed",
        lambda *args, **kwargs: SearchResultBundle(
            results=[(embedding, 0.77)],
            best_vector_similarity=0.0,
            best_keyword_score=1.0,
            has_lexical_signal=True,
            query_variants=["secret number"],
        ),
    )

    class FakeBind:
        url = "sqlite://test"

    class FakeDB:
        bind = FakeBind()

    context = retrieve_context(
        tenant_id=uuid.uuid4(),
        question="secret number",
        db=FakeDB(),
        api_key="sk-test",
    )

    assert context.mode == "hybrid"
    assert context.best_rank_score == 0.77
    assert context.best_confidence_score == 0.0
    assert context.confidence_source == "vector_similarity"
