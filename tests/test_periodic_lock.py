"""Distributed-lock gating for periodic daemon jobs.

Simulates multiple Railway workers contending for the same lock via a shared
in-memory stand-in for ``backend.core.redis`` (SET NX EX + token-checked
delete). Covers: only one worker runs a tick, a crashed holder is recovered
after TTL expiry, per-tick release, and the no-Redis local-dev fallback.
"""

from __future__ import annotations

import logging

import pytest

from backend.jobs._periodic import LockSpec, PeriodicJob


class _FakeLockStore:
    """In-memory Redis lock, shared across "workers" in a test.

    ``acquire`` mirrors ``SET key token NX EX ttl``; ``release`` mirrors the
    token-checked delete. ``expire`` drops a key to simulate TTL lapse (e.g.
    after a holder crashes without releasing).
    """

    def __init__(self) -> None:
        self.enabled = True
        self.locks: dict[str, str] = {}
        self._counter = 0

    def acquire_lock_sync(self, key: str, ttl_seconds: int, *, timeout: float = 3.0) -> str | None:
        if not self.enabled or key in self.locks:
            return None
        self._counter += 1
        token = f"tok-{self._counter}"
        self.locks[key] = token
        return token

    def release_lock_sync(self, key: str, token: str, *, timeout: float = 3.0) -> bool:
        if self.locks.get(key) == token:
            del self.locks[key]
            return True
        return False

    def is_enabled(self) -> bool:
        return self.enabled

    def expire(self, key: str) -> None:
        self.locks.pop(key, None)


@pytest.fixture
def fake_lock(monkeypatch: pytest.MonkeyPatch) -> _FakeLockStore:
    # run_once() imports these from backend.core.redis at call time, so
    # patching the module attributes intercepts the lookup.
    import backend.core.redis as redis_module

    store = _FakeLockStore()
    monkeypatch.setattr(redis_module, "acquire_lock_sync", store.acquire_lock_sync)
    monkeypatch.setattr(redis_module, "release_lock_sync", store.release_lock_sync)
    monkeypatch.setattr(redis_module, "is_enabled", store.is_enabled)
    return store


def _counting_job(*, calls: list[int], lock: LockSpec | None) -> PeriodicJob:
    def _work() -> None:
        calls.append(1)

    return PeriodicJob(
        name="test-job",
        work=_work,
        interval_seconds=3600,
        lock=lock,
    )


def _spec(*, key: str = "lock:test", ttl: int = 60, hold: bool = False) -> LockSpec:
    return LockSpec(
        job_kind="test_job",
        key_factory=lambda: key,
        ttl_seconds=ttl,
        hold=hold,
    )


def test_no_lock_spec_always_runs() -> None:
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=None)
    job.run_once()
    job.run_once()
    assert calls == [1, 1]


def test_parallel_capture_only_one_worker_runs(fake_lock: _FakeLockStore) -> None:
    """Two workers, same tick, same lock key → exactly one runs the work."""
    spec = _spec(hold=True)  # hold so the loser can't grab it after a release
    worker_a_calls: list[int] = []
    worker_b_calls: list[int] = []
    worker_a = _counting_job(calls=worker_a_calls, lock=spec)
    worker_b = _counting_job(calls=worker_b_calls, lock=spec)

    worker_a.run_once()
    worker_b.run_once()

    assert worker_a_calls == [1]
    assert worker_b_calls == []


def test_crash_holder_recovered_after_ttl(fake_lock: _FakeLockStore) -> None:
    """A held lock blocks re-runs until TTL expiry, then the next tick recovers."""
    spec = _spec(key="lock:kb", hold=True)
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=spec)

    job.run_once()  # acquires, holds (simulated crash: never releases)
    job.run_once()  # lock still held → skipped
    assert calls == [1]

    fake_lock.expire("lock:kb")  # TTL lapses
    job.run_once()  # a later tick takes over
    assert calls == [1, 1]


def test_hold_false_releases_after_each_tick(fake_lock: _FakeLockStore) -> None:
    spec = _spec(hold=False)
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=spec)

    job.run_once()
    assert fake_lock.locks == {}  # released
    job.run_once()  # free again → runs
    assert calls == [1, 1]


def test_redis_disabled_runs_unguarded(fake_lock: _FakeLockStore) -> None:
    """No Redis (local dev): bypass the lock and rely on in-process idempotency."""
    fake_lock.enabled = False
    spec = _spec()
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=spec)

    job.run_once()
    job.run_once()

    assert calls == [1, 1]
    assert fake_lock.locks == {}  # never touched the lock


def test_lock_events_are_logged(fake_lock: _FakeLockStore, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="backend.jobs._periodic"):
        # Winner on a released key: acquires then releases.
        _counting_job(calls=[], lock=_spec(hold=False)).run_once()
        # Contend on a held key to force a skip.
        _counting_job(calls=[], lock=_spec(key="lock:busy", hold=True)).run_once()
        _counting_job(calls=[], lock=_spec(key="lock:busy", hold=True)).run_once()

    messages = [r.message for r in caplog.records if "lock_" in r.message]
    assert any("lock_acquired job_kind=test_job" in m for m in messages)
    assert any("lock_released job_kind=test_job" in m for m in messages)
    assert any("lock_skipped job_kind=test_job" in m for m in messages)
    assert all("worker_pid=" in m for m in messages)
