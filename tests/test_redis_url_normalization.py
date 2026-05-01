"""Regression tests for `Settings._normalize_redis_url`.

slowapi 0.1.9 only accepts sync `Storage` — passing a URL with the
`async+` prefix would crash uvicorn at import time. The validator
strips the prefix at the env boundary so both `core/limiter.py` and
`core/redis.py` always receive a plain `redis://` URL.
"""

from __future__ import annotations

import pytest

from backend.core.config import Settings


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        # Plain URLs pass through unchanged.
        ("redis://default:pw@redis.railway.internal:6379", "redis://default:pw@redis.railway.internal:6379"),
        ("rediss://host:6379/0", "rediss://host:6379/0"),
        ("unix:///tmp/redis.sock", "unix:///tmp/redis.sock"),
        # `async+` prefix is stripped (the bug we're guarding against).
        ("async+redis://default:pw@host:6379", "redis://default:pw@host:6379"),
        ("async+rediss://host:6379/0", "rediss://host:6379/0"),
        ("async+unix:///tmp/redis.sock", "unix:///tmp/redis.sock"),
        # Whitespace-only and empty become `None` so callers see "Redis disabled".
        ("", None),
        ("   ", None),
        # `None` (env unset) is preserved.
        (None, None),
    ],
)
def test_normalize_redis_url(env_value: str | None, expected: str | None) -> None:
    assert Settings._normalize_redis_url(env_value) == expected
