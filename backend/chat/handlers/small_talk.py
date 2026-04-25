"""Small-talk handler — placeholder for PR 2/4.

Will encapsulate ``_handle_small_talk_early_exit`` from chat/service.py.
"""

from __future__ import annotations

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler


class SmallTalkHandler(PipelineHandler):
    def can_handle(self, ctx: HandlerContext) -> bool:
        return False

    def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        raise NotImplementedError("SmallTalkHandler will land in PR 2/4 of the chat-pipeline refactor")
