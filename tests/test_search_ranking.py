"""Pure unit tests for the retrieval scoring/ranking primitives: cosine
similarity, BM25/RRF fusion, reranking, script-boost, MMR selection, and score
normalization.
"""

from __future__ import annotations

import uuid

import pytest

from backend.search.service import (
    apply_script_boost,
    async_bm25_search_chunks,
    cosine_similarity,
    detect_query_script_bucket,
    expand_query,
    mmr_select,
)
from backend.search.reranking import rerank_candidates


def test_cosine_similarity_basic() -> None:
    """Identical vectors → 1.0, orthogonal → ~0."""
    vec = [1.0, 0.0, 0.0]
    assert cosine_similarity(vec, vec) == 1.0

    orth_a = [1.0, 0.0, 0.0]
    orth_b = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(orth_a, orth_b)) < 0.001

    # Same direction, different magnitude
    a = [2.0, 0.0, 0.0]
    b = [3.0, 0.0, 0.0]
    assert abs(cosine_similarity(a, b) - 1.0) < 0.001


def test_cosine_similarity_zero_vectors() -> None:
    """Zero vectors → 0.0 (safe handling)."""
    zero = [0.0, 0.0, 0.0]
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(zero, vec) == 0.0
    assert cosine_similarity(vec, zero) == 0.0
    assert cosine_similarity(zero, zero) == 0.0


def test_run_bm25_search_symmetric_merge_deduplicates_hits_and_keeps_earliest_tie_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    first = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="reset password guide")
    second = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="password reset checklist")
    third = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="account recovery flow")

    def fake_score(prepared_corpus, query: str, top_k: int):
        if query == "reset password":
            return [(first, 1.0), (second, 0.6)]
        return [(first, 1.0), (third, 0.9)]

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    bundle = _run_bm25_search(
        [first, second, third],
        query="reset password",
        variant_queries=["reset password", "password reset"],
        top_k=5,
        expansion_mode="symmetric_variants",
    )

    assert [embedding.id for embedding, _ in bundle.results] == [first.id, third.id, second.id]
    assert bundle.merged_hit_count_before_cap == 3
    assert bundle.merged_hit_count_after_cap == 3
    assert bundle.winner_by_id[first.id].variant_index == 0


def test_run_bm25_search_symmetric_mode_can_match_asymmetric_when_no_effective_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    first = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="cors settings")
    second = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="api key rotation")

    def fake_score(prepared_corpus, query: str, top_k: int):
        return [(first, 1.0), (second, 0.5)]

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    asymmetric = _run_bm25_search(
        [first, second],
        query="cors settings",
        variant_queries=["cors settings"],
        top_k=5,
        expansion_mode="asymmetric",
    )
    symmetric = _run_bm25_search(
        [first, second],
        query="cors settings",
        variant_queries=["cors settings", "cors config"],
        top_k=5,
        expansion_mode="symmetric_variants",
    )

    assert symmetric.results == asymmetric.results
    assert symmetric.winner_by_id[first.id].variant_index == 0
    assert symmetric.merged_hit_count_before_cap == asymmetric.merged_hit_count_before_cap


def test_run_bm25_search_applies_cap_after_deterministic_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    first = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="reset password guide")
    second = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="password reset checklist")
    third = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="account recovery")

    def fake_score(prepared_corpus, query: str, top_k: int):
        if query == "reset password":
            return [(first, 1.0)]
        if query == "password reset":
            return [(second, 0.9)]
        return [(third, 0.8)]

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    bundle = _run_bm25_search(
        [first, second, third],
        query="reset password",
        variant_queries=["reset password", "password reset", "account recovery"],
        top_k=2,
        expansion_mode="symmetric_variants",
    )

    assert bundle.merged_hit_count_before_cap == 3
    assert bundle.merged_hit_count_after_cap == 2
    assert [embedding.id for embedding, _ in bundle.results] == [first.id, second.id]


def test_run_bm25_search_uses_final_merged_output_for_lexical_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import _run_bm25_search

    alias_hit = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="alias documentation")

    def fake_score(prepared_corpus, query: str, top_k: int):
        if query == "alias":
            return [(alias_hit, 1.0)]
        return []

    monkeypatch.setattr(
        "backend.search.service._score_prepared_bm25_corpus",
        fake_score,
    )

    bundle = _run_bm25_search(
        [alias_hit],
        query="primary",
        variant_queries=["primary", "alias"],
        top_k=5,
        expansion_mode="symmetric_variants",
    )

    assert bundle.results == [(alias_hit, 1.0)]
    assert bundle.has_lexical_signal is False


def test_expand_query_deduplicates_and_normalizes() -> None:
    variants = expand_query("Reset-password!!   reset password")
    assert variants == [
        "Reset-password!! reset password",
        "Reset password reset password",
        "reset password",
    ]


def test_expand_query_preserves_empty_query_as_single_variant() -> None:
    assert expand_query("") == [""]


def test_rerank_candidates_boosts_lexical_match() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="how to reset your password in the dashboard",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="billing invoice download instructions",
        metadata_json={"chunk_index": 1},
    )

    reranked = rerank_candidates(
        "reset password",
        [(second, 0.9), (first, 0.7)],
        vector_scores={first.id: 0.7, second.id: 0.9},
        bm25_scores={first.id: 1.0, second.id: 0.1},
        top_k=2,
    )

    assert [item[0].id for item in reranked] == [first.id, second.id]
    assert reranked[0][1] > reranked[1][1]


def test_detect_query_script_bucket_distinguishes_cyrillic() -> None:
    assert detect_query_script_bucket("как сбросить пароль") == "cyrillic"
    assert detect_query_script_bucket("reset password") == "latin"


def test_detect_query_script_bucket_separates_every_writing_system() -> None:
    """Each writing system gets its own bucket, not one shared catch-all."""
    samples = [
        "パスワードをリセット",
        "επαναφορά κωδικού",
        "إعادة تعيين كلمة المرور",
        "비밀번호 재설정",
        "重置密码",
        "รีเซ็ตรหัสผ่าน",
        "पासवर्ड रीसेट",
        "איפוס סיסמה",
    ]
    buckets = [detect_query_script_bucket(sample) for sample in samples]
    assert "other" not in buckets
    assert len(set(buckets)) == len(buckets)


def test_detect_query_script_bucket_uses_other_only_without_letters() -> None:
    assert detect_query_script_bucket("12345 — !?") == "other"
    assert detect_query_script_bucket("") == "other"


def test_detect_query_script_bucket_follows_the_dominant_script() -> None:
    """A mixed-script query buckets by majority, not by first script seen."""
    assert detect_query_script_bucket("OpenAI для русского") == "cyrillic"
    assert detect_query_script_bucket("reset password настройки") == "latin"


def test_detect_query_script_bucket_folds_presentation_forms() -> None:
    """Fullwidth/halfwidth/mathematical forms bucket by writing system."""
    assert detect_query_script_bucket("ＡＰＩ") == "latin"
    assert detect_query_script_bucket("ﾊﾛｰ") == "katakana"


def test_apply_script_boost_prefers_matching_script_bucket() -> None:
    from backend.models import Embedding

    english = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings",
        metadata_json={"language": "en"},
    )
    russian = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="сброс пароля в настройках",
        metadata_json={"language": "ru"},
    )

    boosted = apply_script_boost(
        "cyrillic",
        [(english, 0.81), (russian, 0.79)],
        top_k=2,
    )

    assert [item[0].id for item in boosted] == [russian.id, english.id]


def test_apply_script_boost_reads_the_chunk_text_not_its_language_label() -> None:
    from backend.models import Embedding

    english = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings",
        metadata_json={"language": "en"},
    )
    # A stale/wrong language label must not override the chunk's own script.
    mislabeled = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="скинути пароль в налаштуваннях",
        metadata_json={"language": "en"},
    )

    boosted = apply_script_boost(
        "cyrillic",
        [(english, 0.81), (mislabeled, 0.79)],
        top_k=2,
    )

    assert [item[0].id for item in boosted] == [mislabeled.id, english.id]


def test_mmr_select_replaces_near_duplicate_chunk() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )
    duplicate = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel now",
        metadata_json={"chunk_index": 1},
    )
    diverse = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="download billing invoice from account page",
        metadata_json={"chunk_index": 2},
    )

    selection = mmr_select(
        [(first, 0.95), (duplicate, 0.92), (diverse, 0.7)],
        top_k=2,
    )
    selected = selection.results
    replacements = selection.replacements

    assert [item[0].id for item in selected] == [first.id, diverse.id]
    assert selected[0][1] == 0.95
    assert selected[1][1] == 0.7
    assert replacements == [
        {
            "removed_chunk_id": str(duplicate.id),
            "replacement_chunk_id": str(diverse.id),
            "reason": "removed_baseline_redundancy:0.833",
            "removed_redundancy": 0.833333,
            "replacement_redundancy": 0.0,
        }
    ]
    assert selection.diagnostics == [
        {
            "selected_chunk_id": str(first.id),
            "selected_rank": 1,
            "base_score": 0.95,
            "mmr_score": 0.95,
            "redundancy_penalty": 0.0,
        },
        {
            "selected_chunk_id": str(diverse.id),
            "selected_rank": 2,
            "base_score": 0.7,
            "mmr_score": 0.49,
            "redundancy_penalty": 0.0,
        },
    ]


def test_mmr_select_handles_empty_candidates() -> None:
    selection = mmr_select([], top_k=3)

    assert selection.results == []
    assert selection.replacements == []
    assert selection.diagnostics == []


def test_mmr_select_returns_available_candidates_when_fewer_than_top_k(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from backend.models import Embedding

    only = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="single chunk",
        metadata_json={"chunk_index": 0},
    )

    selection = mmr_select([(only, 0.88)], top_k=3)

    assert selection.results == [(only, 0.88)]
    assert any("fewer candidates than requested top_k" in message for message in caplog.messages)


def test_rerank_candidates_uses_widened_bm25_scores_without_zeroing_tail_candidates() -> None:
    from backend.models import Embedding

    first = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password",
        metadata_json={"chunk_index": 0},
    )
    second = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password steps",
        metadata_json={"chunk_index": 1},
    )
    third = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="password reset troubleshooting",
        metadata_json={"chunk_index": 2},
    )

    reranked = rerank_candidates(
        "reset password",
        [(first, 0.9), (second, 0.8), (third, 0.7)],
        vector_scores={first.id: 0.9, second.id: 0.8, third.id: 0.7},
        bm25_scores={first.id: 1.0, second.id: 0.8, third.id: 0.6},
        top_k=3,
    )

    assert len(reranked) == 3
    assert reranked[2][1] > 0.0


@pytest.mark.asyncio
async def test_bm25_search_chunks_finds_match(db_session, async_search_session) -> None:
    """async_bm25_search_chunks returns chunks relevant to query tokens."""
    from tests.test_models import _create_client, _create_user
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    user = _create_user(db_session, email="kw@example.com")
    cl = _create_client(db_session, user, name="KW Tenant")
    doc = Document(
        tenant_id=cl.id,
        filename="cors.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="CORS configuration",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="CORS settings: allow_origins, allow_methods",
        vector=None,
        metadata_json={"chunk_index": 0},
    )
    decoy_one = Embedding(
        document_id=doc.id,
        chunk_text="Billing export guide for invoices",
        vector=None,
        metadata_json={"chunk_index": 1},
    )
    decoy_two = Embedding(
        document_id=doc.id,
        chunk_text="Rotate API keys in dashboard settings",
        vector=None,
        metadata_json={"chunk_index": 2},
    )
    db_session.add_all([emb, decoy_one, decoy_two])
    db_session.commit()

    results = await async_bm25_search_chunks(
        cl.id, "cors settings", top_k=5, db=async_search_session
    )
    # decoy_one ("Billing export guide for invoices") shares no tokens with the
    # query and is filtered out at the SQL layer; only chunks containing at
    # least one query token are scored.
    assert len(results) == 2
    chunk_texts = {emb.chunk_text for emb, _ in results}
    assert chunk_texts == {
        "CORS settings: allow_origins, allow_methods",
        "Rotate API keys in dashboard settings",
    }
    assert results[0][0].chunk_text == "CORS settings: allow_origins, allow_methods"
    assert 0 < results[0][1] <= 1.0
    assert results[0][1] > results[-1][1]


def test_bm25_signal_uses_overlap_fallback_when_raw_scores_are_flat() -> None:
    from backend.models import Embedding
    from backend.search.service import _bm25_score_candidates_with_signal

    matching = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="secret number explanation",
        metadata_json={"chunk_index": 0},
    )
    non_matching = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="billing invoice guide",
        metadata_json={"chunk_index": 1},
    )

    results, has_signal = _bm25_score_candidates_with_signal(
        [matching, non_matching],
        "secret",
        top_k=5,
    )

    assert has_signal is True
    assert [embedding.id for embedding, _ in results] == [matching.id]
    assert results[0][1] == 1.0


def test_normalize_scored_results_flat_multi_doc_returns_zero() -> None:
    """Multiple docs with identical scores: must return 0.0, not 1.0.

    Returning 1.0 artificially inflates every document's fusion contribution
    when the BM25 signal cannot distinguish between them.
    """
    from backend.models import Embedding
    from backend.search.service import _normalize_scored_results

    emb_a = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="a", metadata_json={})
    emb_b = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="b", metadata_json={})
    emb_c = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="c", metadata_json={})

    for flat_score in (0.0, 0.5, 1.0, 42.0):
        result = _normalize_scored_results([(emb_a, flat_score), (emb_b, flat_score), (emb_c, flat_score)])
        scores = [s for _, s in result]
        assert scores == [0.0, 0.0, 0.0], (
            f"Expected all 0.0 when max==min=={flat_score} across multiple docs, got {scores}"
        )


def test_normalize_scored_results_single_item_returns_one() -> None:
    """Single unique match: must return 1.0 — it is the top result by definition."""
    from backend.models import Embedding
    from backend.search.service import _normalize_scored_results

    emb = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="only", metadata_json={})

    for raw_score in (0.0, 0.5, 1.0, 42.0):
        result = _normalize_scored_results([(emb, raw_score)])
        assert result[0][1] == 1.0, (
            f"Expected 1.0 for single-item list with score {raw_score}, got {result[0][1]}"
        )


def test_normalize_scored_results_distinct_scores_are_scaled() -> None:
    """Normal case: scores are rescaled to [0, 1] with order preserved."""
    from backend.models import Embedding
    from backend.search.service import _normalize_scored_results

    emb_high = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="hi", metadata_json={})
    emb_mid = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="mid", metadata_json={})
    emb_low = Embedding(id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_text="lo", metadata_json={})

    result = _normalize_scored_results([(emb_high, 3.0), (emb_mid, 2.0), (emb_low, 1.0)])
    scores = [s for _, s in result]

    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.5)
    assert scores[2] == pytest.approx(0.0)


def test_normalize_scored_results_empty_list_returns_empty() -> None:
    from backend.search.service import _normalize_scored_results

    assert _normalize_scored_results([]) == []


# ── Parallel NER (Step 5+ latency fix) ──────────────────────────────────────
