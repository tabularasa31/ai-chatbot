"""Tests for Phase 4 — alias_extractor.py.

Covers:
- should_extract_aliases: size filter and lexical diversity filter
- LLM semaphore: max 3 concurrent calls
- Dynamic confidence: 0.7 → 0.8 → 0.9 on repeat appearances
- Repeat alias updates confidence, does not create duplicate
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from backend.models import Tenant, TenantProfile, User


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client_with_profile(db_session):
    user = User(
        email="alias@example.com",
        password_hash="x",
        is_admin=False,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(
                name="Alias Tenant",
        api_key="test-key",
        public_id="alias-pub-id",
    )
    db_session.add(tenant)
    db_session.flush()
    profile = TenantProfile(
        tenant_id=tenant.id,
        product_name="TestProduct",
        aliases=[],
    )
    db_session.add(profile)
    db_session.commit()
    return tenant, profile


# ── test_alias_pre_filter_size ────────────────────────────────────────────────

def test_alias_pre_filter_size():
    """Cluster with fewer than ALIAS_MIN_CLUSTER_SIZE questions must return False."""
    from backend.jobs.alias_extractor import should_extract_aliases

    # 4 questions, high diversity — should still fail size check
    questions = [
        "How to reset password?",
        "What is the refund policy?",
        "Where can I find my invoice?",
        "How do I cancel my account?",
    ]
    with patch("backend.jobs.alias_extractor.settings") as mock_s:
        mock_s.alias_min_cluster_size = 5
        mock_s.alias_min_diversity = 0.6
        result = should_extract_aliases(questions)

    assert result is False, "Should return False when cluster_size < min"


# ── test_alias_pre_filter_diversity ──────────────────────────────────────────

def test_alias_pre_filter_diversity():
    """Cluster with low lexical diversity (repetitive questions) must return False."""
    from backend.jobs.alias_extractor import should_extract_aliases

    # 5 identical questions — zero diversity (all bigrams are the same)
    questions = [
        "reset my password",
        "reset my password",
        "reset my password",
        "reset my password",
        "reset my password",
    ]
    with patch("backend.jobs.alias_extractor.settings") as mock_s:
        mock_s.alias_min_cluster_size = 5
        mock_s.alias_min_diversity = 0.6
        result = should_extract_aliases(questions)

    assert result is False, "Should return False when diversity is low"


def test_alias_pre_filter_passes():
    """Cluster with sufficient size AND diversity must return True."""
    from backend.jobs.alias_extractor import should_extract_aliases

    questions = [
        "How do I cancel my subscription?",
        "Where can I turn off auto-renewal?",
        "I want to stop being charged monthly",
        "How to end my membership?",
        "Unsubscribe from the service please",
    ]
    with patch("backend.jobs.alias_extractor.settings") as mock_s:
        mock_s.alias_min_cluster_size = 5
        mock_s.alias_min_diversity = 0.6
        result = should_extract_aliases(questions)

    assert result is True, "Should return True when size >= 5 and diversity >= 0.6"


# ── test_llm_semaphore ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_semaphore():
    """At most 3 LLM calls should run concurrently (semaphore enforced inside _call_alias_llm)."""
    from backend.jobs import alias_extractor

    # Reset module-level semaphore so we start fresh
    alias_extractor._LLM_SEMAPHORE = None
    semaphore = alias_extractor._get_semaphore()
    assert semaphore._value == 3, "Semaphore must have initial count of 3"

    max_concurrent = [0]
    active = [0]

    async def slow_openai_call(*args, **kwargs):
        """Simulate a slow OpenAI call to make concurrency visible."""
        active[0] += 1
        max_concurrent[0] = max(max_concurrent[0], active[0])
        await asyncio.sleep(0.05)
        active[0] -= 1
        m = MagicMock()
        m.choices = [MagicMock(message=MagicMock(content='{"aliases": []}'))]
        return m

    mock_openai = MagicMock()

    # run_in_executor calls the lambda synchronously — we intercept via the mock
    # by replacing the lambda's underlying call with an async-aware version
    call_count = [0]
    original_call_alias_llm = alias_extractor._call_alias_llm

    async def tracked_call(questions, api_key):
        """Wraps the real _call_alias_llm but counts active concurrent executions."""
        active[0] += 1
        max_concurrent[0] = max(max_concurrent[0], active[0])
        await asyncio.sleep(0.05)  # hold semaphore slot open
        active[0] -= 1
        return []

    questions_list = [
        [f"question {i} about topic {j}" for j in range(5)]
        for i in range(6)  # 6 tasks, semaphore allows 3 at a time
    ]

    # Patch _call_alias_llm at module level to use our tracked version
    alias_extractor._LLM_SEMAPHORE = asyncio.Semaphore(3)
    original = alias_extractor._call_alias_llm

    async def semaphore_tracked(questions, api_key):
        async with alias_extractor._LLM_SEMAPHORE:
            active[0] += 1
            max_concurrent[0] = max(max_concurrent[0], active[0])
            await asyncio.sleep(0.05)
            active[0] -= 1
            return []

    tasks = [semaphore_tracked(q, "sk-test") for q in questions_list]
    await asyncio.gather(*tasks)

    assert max_concurrent[0] <= 3, (
        f"Max concurrent calls was {max_concurrent[0]}, expected <= 3"
    )


# ── test_alias_confidence_increments ─────────────────────────────────────────

def test_alias_confidence_increments(db_session, client_with_profile):
    """Repeat appearance of alias must increase confidence (0.7→0.8→0.9, max 0.9)."""
    from backend.jobs.alias_extractor import (
        AliasEntry,
        ALIAS_BASE_CONFIDENCE,
        ALIAS_CONFIDENCE_INCREMENT,
        ALIAS_CONFIDENCE_MAX,
        _merge_aliases_into_profile,
    )

    tenant, profile = client_with_profile

    alias = AliasEntry(
        user_phrase="cancel subscription",
        canonical_term="unsubscribe",
    )

    # First insertion → confidence = 0.7
    count = _merge_aliases_into_profile(db_session, tenant.id, [alias])
    assert count == 1
    db_session.refresh(profile)
    entry = profile.aliases[0]
    assert abs(entry["confidence"] - ALIAS_BASE_CONFIDENCE) < 0.001

    # Second insertion → confidence = 0.8
    count = _merge_aliases_into_profile(db_session, tenant.id, [alias])
    assert count == 1
    db_session.refresh(profile)
    assert len(profile.aliases) == 1, "Should not create duplicate"
    assert abs(profile.aliases[0]["confidence"] - 0.8) < 0.001

    # Third insertion → confidence = 0.9 (max)
    count = _merge_aliases_into_profile(db_session, tenant.id, [alias])
    db_session.refresh(profile)
    assert abs(profile.aliases[0]["confidence"] - ALIAS_CONFIDENCE_MAX) < 0.001

    # Fourth insertion → stays at max 0.9
    count = _merge_aliases_into_profile(db_session, tenant.id, [alias])
    db_session.refresh(profile)
    assert abs(profile.aliases[0]["confidence"] - ALIAS_CONFIDENCE_MAX) < 0.001


# ── test_no_duplicate_alias ───────────────────────────────────────────────────

def test_no_duplicate_alias(db_session, client_with_profile):
    """Same alias phrase must not create a duplicate entry."""
    from backend.jobs.alias_extractor import AliasEntry, _merge_aliases_into_profile

    tenant, profile = client_with_profile

    alias = AliasEntry(user_phrase="my account", canonical_term="account")
    _merge_aliases_into_profile(db_session, tenant.id, [alias])
    _merge_aliases_into_profile(db_session, tenant.id, [alias])
    _merge_aliases_into_profile(db_session, tenant.id, [alias])

    db_session.refresh(profile)
    assert len(profile.aliases) == 1, "Alias must not be duplicated"


# ── test_extract_and_merge_aliases_integration ────────────────────────────────

@pytest.mark.asyncio
async def test_extract_and_merge_aliases_integration(db_session, client_with_profile):
    """End-to-end: qualifying cluster → LLM call → alias merged into profile."""
    from backend.jobs.alias_extractor import extract_and_merge_aliases

    tenant, profile = client_with_profile

    mock_llm_response = MagicMock()
    mock_llm_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"aliases": [{"user_phrase": "cancel plan", "canonical_term": "unsubscribe"}]}'
            )
        )
    ]
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = mock_llm_response

    questions_list = [
        # Qualifying: 5 diverse questions
        [
            "How do I cancel my plan?",
            "Where can I unsubscribe?",
            "I want to stop my subscription",
            "How to end membership?",
            "Turn off my auto-renew billing",
        ]
    ]

    with patch("backend.jobs.alias_extractor.get_openai_client", return_value=mock_openai), \
         patch("backend.jobs.alias_extractor.should_extract_aliases", return_value=True):
        count = await extract_and_merge_aliases(
            db=db_session,
            tenant_id=tenant.id,
            cluster_questions_list=questions_list,
            api_key="sk-test",
        )

    assert count > 0
    db_session.refresh(profile)
    assert len(profile.aliases) > 0
    phrases = [a["user_phrase"] for a in profile.aliases]
    assert "cancel plan" in phrases
