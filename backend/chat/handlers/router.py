"""Pipeline handler router."""

from __future__ import annotations

from collections.abc import Iterable

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.chat.handlers.escalation import EscalationStateMachine
from backend.chat.handlers.greeting import GreetingHandler
from backend.chat.handlers.rag import RagHandler
from backend.chat.handlers.small_talk import SmallTalkHandler


class HandlerRouter:
    """Dispatches a HandlerContext to the first handler whose can_handle is True.

    Returns the handler's ChatTurnOutcome, or None when no handler matched —
    the caller is expected to fall through to legacy logic in that case.
    """

    def __init__(self, handlers: Iterable[PipelineHandler]) -> None:
        self._handlers: list[PipelineHandler] = list(handlers)

    @property
    def handlers(self) -> tuple[PipelineHandler, ...]:
        """Read-only view of the registered handler chain, in dispatch order."""
        return tuple(self._handlers)

    def dispatch(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        for handler in self._handlers:
            if handler.can_handle(ctx):
                outcome = handler.handle(ctx)
                if outcome is not None:
                    return outcome
                # Fall through to the next handler when this one opted out
                # at runtime (e.g. T-3 escalation failed → retry with RAG).
        return None


def default_router() -> HandlerRouter:
    """Builds the standard handler chain.

    Order matters: GreetingHandler claims empty + new-session turns first,
    SmallTalkHandler claims single-word turns outside escalation flows,
    EscalationStateMachine claims any active escalation state or explicit
    human request, and RagHandler is the catch-all that runs the full RAG
    pipeline for everything else.
    """
    return HandlerRouter(
        [
            GreetingHandler(),
            SmallTalkHandler(),
            EscalationStateMachine(),
            RagHandler(),
        ]
    )
