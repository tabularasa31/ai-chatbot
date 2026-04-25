"""Escalation handler — placeholder for PR 4/4.

Will encapsulate the FI-ESC state machine (clarify → contact-collect →
ticket-create → handoff) currently inlined in chat/service.py.
"""

from __future__ import annotations

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler


class EscalationStateMachine(PipelineHandler):
    def can_handle(self, ctx: HandlerContext) -> bool:
        return False

    def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        raise NotImplementedError(
            "EscalationStateMachine will land in PR 4/4 of the chat-pipeline refactor"
        )
