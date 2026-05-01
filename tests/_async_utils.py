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
