"""Verify chat_escalated PostHog event fires with correct properties.

The event-shape and tenant-isolation-guard cases are covered end to end
(through the metrics service + a fake PostHog client) in
tests/test_chat_posthog_events.py::test_chat_escalated_has_trigger_and_plan_tier
and ::test_chat_escalated_tenant_isolation_required. What is not covered
there is telemetry resilience: a PostHog outage must never break the chat
turn that triggered the escalation.
"""

from __future__ import annotations

from backend.chat.events import _emit_chat_escalated_event


def test_telemetry_failure_does_not_propagate(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("posthog down")

    monkeypatch.setattr("backend.chat.events.capture_event", boom)

    # Must not raise.
    _emit_chat_escalated_event(
        tenant_public_id="tnt_test",
        bot_public_id=None,
        chat_id="chat_test",
        escalation_reason="explicit_human_request",
    )
