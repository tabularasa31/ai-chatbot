"""Chat pipeline handlers — pluggable stages dispatched by HandlerRouter.

The standard chain is GreetingHandler → EscalationStateMachine → RagHandler
(see ``default_router``); every non-empty turn that no earlier handler claims
falls through to the full RAG pipeline.
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
