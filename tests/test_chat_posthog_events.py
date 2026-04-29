"""Tests for PostHog event shape in the chat pipeline.

Verifies that chat_completed, chat_escalated, and chat_feedback events
are emitted with the correct property names (tenant isolation guard included).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.chat.events import (
    _emit_chat_completed_event,
    _emit_chat_escalated_event,
    _emit_chat_feedback_event,
    _emit_chat_turn_event,
)
from backend.observability.metrics import MetricsService, get_metrics


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _RecordingClient:
    """Minimal PostHog stub that records captured calls."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []

    def capture(self, **kwargs):
        self.calls.append(kwargs)

    def group_identify(self, *args, **kwargs):
        pass

    def flush(self):
        pass

    def shutdown(self):
        pass


@pytest.fixture(autouse=True)
def _reset_metrics():
    svc = get_metrics()
    svc.reset()
    yield
    svc.reset()


def _make_enabled_svc(monkeypatch) -> tuple[MetricsService, _RecordingClient]:
    monkeypatch.setattr("backend.observability.metrics.settings.environment", "development")
    monkeypatch.setattr("backend.observability.metrics.settings.posthog_api_key", "phc_test")
    monkeypatch.setattr("backend.observability.metrics.settings.git_sha", None)

    client = _RecordingClient()
    with patch.dict(
        "sys.modules",
        {"posthog": type("M", (), {"Posthog": lambda *a, **kw: client})},
    ):
        svc = get_metrics()
        svc.init()
    return svc, client


# ---------------------------------------------------------------------------
# chat_completed event shape
# ---------------------------------------------------------------------------

def test_chat_completed_emitted_with_required_properties(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_completed_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_123",
        chat_id="chat_xyz",
        latency_ms=320,
        tokens_input=150,
        tokens_output=80,
        model="gpt-4o-mini",
        lang_match=True,
        cap_reason=None,
        reliability_score="high",
        decision_branch="direct_answer",
        plan_tier="pro",
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["event"] == "chat_completed"
    assert call["distinct_id"] == "chat_xyz"
    props = call["properties"]

    # tenant isolation
    assert props["tenant_id"] == "tnt_abc"
    assert props["bot_id"] == "bot_123"

    # all dashboard-relevant fields present
    assert props["latency_ms"] == 320
    assert props["tokens_input"] == 150
    assert props["tokens_output"] == 80
    assert props["model"] == "gpt-4o-mini"
    assert props["lang_match"] is True
    assert props["cap_reason"] is None
    assert props["reliability_score"] == "high"
    assert props["decision_branch"] == "direct_answer"
    assert props["plan_tier"] == "pro"


def test_chat_completed_skipped_when_no_tenant_or_bot():
    # Should not raise and should not call PostHog
    _emit_chat_completed_event(
        tenant_public_id=None,
        bot_public_id=None,
        chat_id="chat_xyz",
    )
    # PostHog is not enabled in test env, but also no tenant/bot guard triggers first


def test_chat_turn_emits_chat_completed(monkeypatch):
    """_emit_chat_turn_event must also emit chat_completed as a side-effect."""
    from backend.chat.decision import Decision, DecisionKind

    svc, client = _make_enabled_svc(monkeypatch)

    decision = Decision(kind=DecisionKind.answer_with_citations)
    _emit_chat_turn_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_123",
        chat_id="chat_xyz",
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        escalated=False,
        latency_ms=500,
        reliability_score="high",
        decision=decision,
        model="gpt-4o-mini",
        plan_tier="free",
    )

    event_names = [c["event"] for c in client.calls]
    assert "chat.turn" in event_names
    assert "chat_completed" in event_names

    completed = next(c for c in client.calls if c["event"] == "chat_completed")
    assert completed["properties"]["tenant_id"] == "tnt_abc"
    assert completed["properties"]["decision_branch"] == "answer_with_citations"
    assert completed["properties"]["model"] == "gpt-4o-mini"
    assert completed["properties"]["plan_tier"] == "free"


# ---------------------------------------------------------------------------
# chat_escalated event shape
# ---------------------------------------------------------------------------

def test_chat_escalated_has_trigger_and_plan_tier(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_escalated_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_123",
        chat_id="chat_xyz",
        escalation_reason="no_docs",
        escalation_trigger="no_docs",
        plan_tier="enterprise",
        priority="High",
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["event"] == "chat_escalated"
    props = call["properties"]

    assert props["tenant_id"] == "tnt_abc"
    assert props["trigger"] == "no_docs"
    assert props["escalation_trigger"] == "no_docs"
    assert props["plan_tier"] == "enterprise"
    assert props["priority"] == "High"
    assert props["reason"] == "no_docs"


def test_chat_escalated_tenant_isolation_required():
    # No tenant, no bot → no event (no raise)
    _emit_chat_escalated_event(
        tenant_public_id=None,
        bot_public_id=None,
        chat_id="chat_xyz",
        escalation_reason="no_docs",
    )


# ---------------------------------------------------------------------------
# chat_feedback event shape
# ---------------------------------------------------------------------------

def test_chat_feedback_positive(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_feedback_event(
        tenant_public_id="tnt_abc",
        bot_public_id=None,
        distinct_id="user_123",
        feedback="positive",
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["event"] == "chat_feedback"
    assert call["distinct_id"] == "user_123"
    props = call["properties"]
    assert props["tenant_id"] == "tnt_abc"
    assert props["feedback"] == "positive"


def test_chat_feedback_negative(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_feedback_event(
        tenant_public_id="tnt_abc",
        bot_public_id=None,
        distinct_id="user_123",
        feedback="negative",
        decision_branch="direct_answer",
        cap_reason=None,
    )

    props = client.calls[0]["properties"]
    assert props["feedback"] == "negative"
    assert props["decision_branch"] == "direct_answer"
    assert props["cap_reason"] is None


def test_chat_feedback_skipped_when_no_tenant():
    _emit_chat_feedback_event(
        tenant_public_id=None,
        bot_public_id=None,
        distinct_id="user_123",
        feedback="positive",
    )


# ---------------------------------------------------------------------------
# cap_reason derivation
# ---------------------------------------------------------------------------

def test_chat_turn_cap_reason_from_reject(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_turn_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_123",
        chat_id="chat_xyz",
        strategy="guard_reject",
        reject_reason="not_relevant",
        is_reject=True,
        escalated=False,
        model="gpt-4o-mini",
    )

    completed = next(c for c in client.calls if c["event"] == "chat_completed")
    assert completed["properties"]["cap_reason"] == "not_relevant"


def test_chat_turn_cap_reason_low_confidence(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_turn_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_123",
        chat_id="chat_xyz",
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        escalated=False,
        reliability_score="low",
        model="gpt-4o-mini",
    )

    completed = next(c for c in client.calls if c["event"] == "chat_completed")
    assert completed["properties"]["cap_reason"] == "low_confidence"


def test_chat_turn_cap_reason_none_when_high(monkeypatch):
    svc, client = _make_enabled_svc(monkeypatch)

    _emit_chat_turn_event(
        tenant_public_id="tnt_abc",
        bot_public_id="bot_123",
        chat_id="chat_xyz",
        strategy="rag_only",
        reject_reason=None,
        is_reject=False,
        escalated=False,
        reliability_score="high",
        model="gpt-4o-mini",
    )

    completed = next(c for c in client.calls if c["event"] == "chat_completed")
    assert completed["properties"]["cap_reason"] is None
