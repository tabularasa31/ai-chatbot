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
