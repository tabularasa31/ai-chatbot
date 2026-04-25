"""Pipeline handler router."""

from __future__ import annotations

from collections.abc import Iterable

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.handlers.greeting import GreetingHandler
from backend.chat.handlers.small_talk import SmallTalkHandler


class HandlerRouter:
    """Dispatches a HandlerContext to the first handler whose can_handle is True.

    Returns the handler's ChatTurnOutcome, or None when no handler matched —
    the caller is expected to fall through to legacy logic in that case.
    """

    def __init__(self, handlers: Iterable[PipelineHandler]) -> None:
        self._handlers: list[PipelineHandler] = list(handlers)

    def dispatch(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        for handler in self._handlers:
            if handler.can_handle(ctx):
                return handler.handle(ctx)
        return None


def default_router() -> HandlerRouter:
    """Builds the standard handler chain.

    Order matters: GreetingHandler claims empty + new-session turns first;
    SmallTalkHandler claims single-word turns outside escalation flows.
    Rag / Escalation handlers are added by subsequent PRs in the chat-pipeline
    refactor epic.
    """
    return HandlerRouter([GreetingHandler(), SmallTalkHandler()])
