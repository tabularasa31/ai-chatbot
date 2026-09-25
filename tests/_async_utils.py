"""Test-only async helpers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from backend.core import db as core_db
from backend.chat.service import async_process_chat_message


async def run_chat_turn(
    tenant_id: uuid.UUID,
    question: str,
    session_id: uuid.UUID,
    db_session: Any,
    **kwargs: Any,
) -> Any:
    """Drive a chat turn through the real async pipeline from a sync test.

    Mirrors what the removed sync ``process_chat_message`` shim did: commit
    the caller's sync session so SQLite releases its locks, run the turn on
    its own ``AsyncSession`` (rebound to the test engine by the ``tenant``
    fixture), then expire the sync session so later reads see the writes.
    """
    db_session.commit()
    async with core_db.AsyncSessionLocal() as async_db:
        result = await async_process_chat_message(
            tenant_id, question, session_id, async_db, **kwargs
        )
    db_session.expire_all()
    return result


def as_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a sync callable so it can be passed as an async monkeypatch.

    Existing tests build inline ``lambda``s for ``monkeypatch.setattr`` calls;
    after migration to native async helpers (``async_match_faq`` etc.) the
    patch target awaits the result. This helper adapts a sync callable into
    an async one without rewriting each lambda.
    """

    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return _wrapped


def async_assert_not_called(name: str) -> Callable[..., Any]:
    """Return an async stub that raises ``AssertionError`` when called.

    Use as a monkeypatch target for async helpers that the test asserts
    must *not* be invoked (e.g. ``async_retrieve_context`` after a guard
    reject). Reads more clearly than the equivalent
    ``_as_async(lambda *a, **kw: (_ for _ in ()).throw(...))`` chain.
    """

    async def _raiser(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"{name} should not have been called")

    return _raiser


def as_async_generate(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Adapt a legacy sync ``generate_answer`` fake to the async 8-tuple contract.

    The sync ``generate_answer`` twin (removed with the async-only migration)
    returned ``(text, total_tokens)``; ``async_generate_answer`` returns
    ``(text, total_tokens, input_tokens, output_tokens, offered_ticket,
    needs_human, clarifying, checklist)``. Wraps the old-style fake for
    ``monkeypatch.setattr("backend.chat.steps.generate.async_generate_answer", ...)``
    and pads any shorter tuple to the current shape.
    """

    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        out = fn(*args, **kwargs)
        if isinstance(out, tuple) and len(out) == 2:
            text, total = out
            return (text, total, 0, 0, False, False, False, False)
        # Fakes that predate a marker default it to absent.
        if isinstance(out, tuple) and 5 <= len(out) < 8:
            return (*out, *(False,) * (8 - len(out)))
        return out

    return _wrapped
