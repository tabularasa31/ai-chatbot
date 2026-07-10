"""Test-only async helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


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
    """Adapt a legacy sync ``generate_answer`` fake to the async 5-tuple contract.

    The sync ``generate_answer`` twin (removed with the async-only migration)
    returned ``(text, total_tokens)``; ``async_generate_answer`` returns
    ``(text, total_tokens, input_tokens, output_tokens, offered_ticket)``.
    Wraps the old-style fake for
    ``monkeypatch.setattr("backend.chat.handlers.rag.async_generate_answer", ...)``
    and normalizes a 2-tuple result to the 5-tuple shape.
    """

    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        out = fn(*args, **kwargs)
        if isinstance(out, tuple) and len(out) == 2:
            text, total = out
            return (text, total, 0, 0, False)
        return out

    return _wrapped
