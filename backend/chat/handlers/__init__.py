"""Chat pipeline handlers — pluggable stages dispatched by HandlerRouter.

Part of the chat/service.py refactor (epic: split god-module into pipeline
objects). PR 1/4 wires only GreetingHandler; SmallTalk / Rag / Escalation
handlers land in subsequent PRs.
"""

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.handlers.router import HandlerRouter, default_router

__all__ = [
    "ChatTurnOutcome",
    "HandlerContext",
    "HandlerRouter",
    "PipelineHandler",
    "default_router",
]
