"""RAG handler — placeholder for PR 3/4.

Will encapsulate ``run_chat_pipeline`` and the retrieve / generate / validate
chain from chat/service.py.
"""

from __future__ import annotations

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler


class RagHandler(PipelineHandler):
    def can_handle(self, ctx: HandlerContext) -> bool:
        return False

    def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        raise NotImplementedError("RagHandler will land in PR 3/4 of the chat-pipeline refactor")
