"""Unit tests for `backend.core.idempotency.idempotent_section`.

These exercise the helper directly with a Redis stub, so the contract holds
regardless of which endpoint adopts it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException, Request

from backend.core import idempotency
from backend.core.idempotency import idempotent_section


# ---------------------------------------------------------------------------
# Redis stub
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory stand-in for the helpers in `backend.core.redis`."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.locks: dict[str, str] = {}
        self.enabled = True

    async def cache_get(self, key: str) -> str | None:
        return self.store.get(key)

    async def cache_set_with_ttl(self, key: str, value: str, ttl_seconds: int) -> bool:
        self.store[key] = value
        return True

    async def acquire_lock(self, key: str, ttl_seconds: int) -> str | None:
        if key in self.locks:
            return None
        token = f"tok-{len(self.locks)}"
        self.locks[key] = token
        return token

    async def release_lock(self, key: str, token: str) -> bool:
        if self.locks.get(key) == token:
            del self.locks[key]
            return True
        return False

    def is_enabled(self) -> bool:
        return self.enabled


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(idempotency.redis_module, "cache_get", fake.cache_get)
    monkeypatch.setattr(idempotency.redis_module, "cache_set_with_ttl", fake.cache_set_with_ttl)
    monkeypatch.setattr(idempotency.redis_module, "acquire_lock", fake.acquire_lock)
    monkeypatch.setattr(idempotency.redis_module, "release_lock", fake.release_lock)
    monkeypatch.setattr(idempotency.redis_module, "is_enabled", fake.is_enabled)
    return fake


# ---------------------------------------------------------------------------
# Request helper
# ---------------------------------------------------------------------------


def _request(idempotency_key: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if idempotency_key is not None:
        headers.append((b"idempotency-key", idempotency_key.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_header_is_noop(fake_redis: _FakeRedis) -> None:
    request = _request(None)
    async with idempotent_section(request, tenant_id="t1", scope="chat") as section:
        assert section.cached is None
        assert section.active is False
        await section.record(status_code=200, body={"ok": True})
    assert fake_redis.store == {}
    assert fake_redis.locks == {}


@pytest.mark.asyncio
async def test_redis_disabled_is_noop(fake_redis: _FakeRedis) -> None:
    fake_redis.enabled = False
    request = _request("abc")
    async with idempotent_section(request, tenant_id="t1", scope="chat") as section:
        assert section.cached is None
        assert section.active is False
        await section.record(status_code=200, body={"ok": True})
    assert fake_redis.store == {}


@pytest.mark.asyncio
async def test_first_request_records_and_releases_lock(fake_redis: _FakeRedis) -> None:
    request = _request("k1")
    async with idempotent_section(request, tenant_id="t1", scope="chat") as section:
        assert section.cached is None
        assert section.active is True
        # Lock acquired during the section.
        assert "idempotency:chat:t1:k1:lock" in fake_redis.locks
        await section.record(status_code=200, body={"text": "hello"})

    # Lock released on exit; response cached.
    assert fake_redis.locks == {}
    raw = fake_redis.store["idempotency:chat:t1:k1:response"]
    assert json.loads(raw) == {"status_code": 200, "body": {"text": "hello"}}


@pytest.mark.asyncio
async def test_replay_returns_cached_response(fake_redis: _FakeRedis) -> None:
    request = _request("k1")
    async with idempotent_section(request, tenant_id="t1", scope="chat") as section:
        await section.record(status_code=200, body={"text": "first"})

    # Second call with the same key replays without acquiring the lock.
    request2 = _request("k1")
    async with idempotent_section(request2, tenant_id="t1", scope="chat") as section:
        assert section.cached is not None
        assert section.cached.status_code == 200
        assert section.cached.body == {"text": "first"}
    assert fake_redis.locks == {}


@pytest.mark.asyncio
async def test_keys_scoped_per_tenant(fake_redis: _FakeRedis) -> None:
    async with idempotent_section(_request("k1"), tenant_id="t1", scope="chat") as section:
        await section.record(status_code=200, body={"who": "tenant-1"})

    async with idempotent_section(_request("k1"), tenant_id="t2", scope="chat") as section:
        # Same Idempotency-Key, different tenant — must not replay.
        assert section.cached is None


@pytest.mark.asyncio
async def test_keys_scoped_per_scope(fake_redis: _FakeRedis) -> None:
    async with idempotent_section(_request("k1"), tenant_id="t1", scope="chat") as section:
        await section.record(status_code=200, body={"scope": "chat"})

    async with idempotent_section(_request("k1"), tenant_id="t1", scope="escalate") as section:
        # Same key + tenant, different scope — independent.
        assert section.cached is None


@pytest.mark.asyncio
async def test_parallel_duplicate_returns_409_when_sibling_does_not_finish(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First request acquires the lock and "hangs" — we never call section.record.
    # We simulate the second arrival by entering a nested section while the
    # first is still active.
    monkeypatch.setattr(idempotency, "PARALLEL_POLL_TOTAL_SECONDS", 0.1)
    monkeypatch.setattr(idempotency, "PARALLEL_POLL_INTERVAL_SECONDS", 0.02)

    async def first_holds_lock(barrier: asyncio.Event, done: asyncio.Event) -> None:
        async with idempotent_section(
            _request("k1"), tenant_id="t1", scope="chat"
        ) as section:
            assert section.active
            barrier.set()
            await done.wait()

    barrier = asyncio.Event()
    done = asyncio.Event()
    holder = asyncio.create_task(first_holds_lock(barrier, done))
    await barrier.wait()

    with pytest.raises(HTTPException) as exc_info:
        async with idempotent_section(
            _request("k1"), tenant_id="t1", scope="chat"
        ) as _section:
            pytest.fail("should not enter")  # pragma: no cover
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "idempotency_in_flight"

    done.set()
    await holder


@pytest.mark.asyncio
async def test_parallel_duplicate_replays_when_sibling_finishes(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set tight polling so the test runs quickly.
    monkeypatch.setattr(idempotency, "PARALLEL_POLL_TOTAL_SECONDS", 1.0)
    monkeypatch.setattr(idempotency, "PARALLEL_POLL_INTERVAL_SECONDS", 0.02)

    async def first_finishes_after_delay(barrier: asyncio.Event) -> None:
        async with idempotent_section(
            _request("k1"), tenant_id="t1", scope="chat"
        ) as section:
            barrier.set()
            await asyncio.sleep(0.1)
            await section.record(status_code=200, body={"text": "from sibling"})

    barrier = asyncio.Event()
    holder = asyncio.create_task(first_finishes_after_delay(barrier))
    await barrier.wait()

    async with idempotent_section(
        _request("k1"), tenant_id="t1", scope="chat"
    ) as section:
        # The sibling stored the response while we were polling — replay it.
        assert section.cached is not None
        assert section.cached.body == {"text": "from sibling"}

    await holder


@pytest.mark.asyncio
async def test_oversized_header_treated_as_missing(fake_redis: _FakeRedis) -> None:
    request = _request("x" * (idempotency.MAX_KEY_LENGTH + 1))
    async with idempotent_section(request, tenant_id="t1", scope="chat") as section:
        assert section.active is False
        await section.record(status_code=200, body={"ok": True})
    assert fake_redis.store == {}


@pytest.mark.asyncio
async def test_empty_header_treated_as_missing(fake_redis: _FakeRedis) -> None:
    request = _request("   ")
    async with idempotent_section(request, tenant_id="t1", scope="chat") as section:
        assert section.active is False


@pytest.mark.asyncio
async def test_corrupt_cache_value_is_treated_as_miss(
    fake_redis: _FakeRedis,
) -> None:
    fake_redis.store["idempotency:chat:t1:k1:response"] = "not-json"

    async with idempotent_section(
        _request("k1"), tenant_id="t1", scope="chat"
    ) as section:
        # Corrupt cache → cache miss → handler runs again.
        assert section.cached is None
        assert section.active is True


@pytest.mark.asyncio
async def test_lock_released_even_when_handler_raises(fake_redis: _FakeRedis) -> None:
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        async with idempotent_section(
            _request("k1"), tenant_id="t1", scope="chat"
        ) as section:
            assert section.active
            raise Boom()

    assert fake_redis.locks == {}
    assert "idempotency:chat:t1:k1:response" not in fake_redis.store
