"""Distributed-lock gating for periodic daemon jobs.

Simulates multiple Railway workers contending for the same lock via a shared
in-memory stand-in for ``backend.core.redis`` (SET NX EX + token-checked
delete, plus a marker cache). Covers: mutual exclusion during a run, recovery
after a crashed holder's TTL lapses, the durable done-marker that makes a
once-per-window job single-run even after the short lock expires, lock release
on failure, and the no-Redis local-dev fallback.
"""

from __future__ import annotations

import logging

import pytest

from backend.jobs._periodic import LockSpec, PeriodicJob


class _FakeRedis:
    """In-memory Redis lock + marker cache shared across "workers" in a test.

    ``acquire`` mirrors ``SET key token NX EX ttl``; ``release`` mirrors the
    token-checked delete. ``expire``/``expire_marker`` drop a key to simulate a
    TTL lapse (a crashed holder, or the next window rolling over).
    """

    def __init__(self) -> None:
        self.enabled = True
        self.locks: dict[str, str] = {}
        self.markers: dict[str, str] = {}
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

    def cache_get_sync(self, key: str, *, timeout: float = 3.0) -> str | None:
        return self.markers.get(key)

    def cache_set_sync(self, key: str, value: str, ttl_seconds: int, *, timeout: float = 3.0) -> bool:
        self.markers[key] = value
        return True

    def is_enabled(self) -> bool:
        return self.enabled

    def expire(self, key: str) -> None:
        self.locks.pop(key, None)

    def expire_marker(self, key: str) -> None:
        self.markers.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    # run_once() imports these from backend.core.redis at call time, so
    # patching the module attributes intercepts the lookup.
    import backend.core.redis as redis_module

    fake = _FakeRedis()
    monkeypatch.setattr(redis_module, "acquire_lock_sync", fake.acquire_lock_sync)
    monkeypatch.setattr(redis_module, "release_lock_sync", fake.release_lock_sync)
    monkeypatch.setattr(redis_module, "cache_get_sync", fake.cache_get_sync)
    monkeypatch.setattr(redis_module, "cache_set_sync", fake.cache_set_sync)
    monkeypatch.setattr(redis_module, "is_enabled", fake.is_enabled)
    return fake


def _job(*, work, lock: LockSpec | None) -> PeriodicJob:
    return PeriodicJob(name="test-job", work=work, interval_seconds=3600, lock=lock)


def _counting_job(*, calls: list[int], lock: LockSpec | None) -> PeriodicJob:
    return _job(work=lambda: calls.append(1), lock=lock)


def _excl_spec(*, key: str = "lock:test", ttl: int = 60) -> LockSpec:
    """Mutual-exclusion only (sweeper-style): no done-marker."""
    return LockSpec(job_kind="test_job", key_factory=lambda: key, ttl_seconds=ttl)


def _window_spec(*, key: str = "lock:kb", marker: str = "done:kb") -> LockSpec:
    """Once-per-window (kb_snapshot-style): short lock + durable marker."""
    return LockSpec(
        job_kind="kb_job",
        key_factory=lambda: key,
        ttl_seconds=600,
        done_marker_factory=lambda: marker,
        done_ttl_seconds=26 * 3600,
    )


def test_marker_without_ttl_is_rejected() -> None:
    """A done-marker with no TTL would silently never persist — fail loudly."""
    with pytest.raises(ValueError, match="done_ttl_seconds"):
        LockSpec(
            job_kind="misconfigured",
            key_factory=lambda: "lock:x",
            ttl_seconds=60,
            done_marker_factory=lambda: "done:x",
        )


def test_no_lock_spec_always_runs() -> None:
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=None)
    job.run_once()
    job.run_once()
    assert calls == [1, 1]


def test_mutual_exclusion_while_lock_held(fake_redis: _FakeRedis) -> None:
    """A second worker ticking while the first holds the lock is skipped."""
    spec = _excl_spec()
    b_calls: list[int] = []
    worker_b = _counting_job(calls=b_calls, lock=spec)

    a_calls: list[int] = []

    def a_work() -> None:
        a_calls.append(1)
        # B ticks while A is still mid-work and holding the lock.
        worker_b.run_once()

    worker_a = _job(work=a_work, lock=spec)
    worker_a.run_once()

    assert a_calls == [1]
    assert b_calls == []  # B could not acquire while A held it
    assert fake_redis.locks == {}  # A released on the way out


def test_dead_holder_recovered_after_ttl(fake_redis: _FakeRedis) -> None:
    """A killed holder's lock (never released) blocks until TTL, then recovers."""
    spec = _excl_spec(key="lock:x")
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=spec)

    fake_redis.locks["lock:x"] = "dead-worker-token"  # crashed holder still holds
    job.run_once()
    assert calls == []  # skipped while the stale lock lingers

    fake_redis.expire("lock:x")  # TTL lapses
    job.run_once()
    assert calls == [1]


def test_lock_released_when_work_raises(fake_redis: _FakeRedis) -> None:
    """hold-on-success: a failed tick frees the lock so the next tick retries."""
    spec = _excl_spec(key="lock:y")

    def boom() -> None:
        raise RuntimeError("db hiccup")

    job = _job(work=boom, lock=spec)

    with pytest.raises(RuntimeError):
        job.run_once()
    assert fake_redis.locks == {}  # released despite the failure

    # Next tick can acquire and run.
    calls: list[int] = []
    _counting_job(calls=calls, lock=spec).run_once()
    assert calls == [1]


def test_done_marker_makes_run_once_per_window(fake_redis: _FakeRedis) -> None:
    spec = _window_spec()
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=spec)

    job.run_once()  # runs, writes marker, releases lock
    assert calls == [1]
    assert fake_redis.locks == {}
    assert "done:kb" in fake_redis.markers

    job.run_once()  # marker present → work skipped, lock still released
    assert calls == [1]
    assert fake_redis.locks == {}

    fake_redis.expire_marker("done:kb")  # next window rolls over
    job.run_once()
    assert calls == [1, 1]


def test_race_loser_does_not_reemit_after_lock_expiry(fake_redis: _FakeRedis) -> None:
    """The bug the marker fixes: a worker that lost the race must not re-run
    the once-daily work when the short lock later expires."""
    spec = _window_spec()
    a_calls: list[int] = []
    b_calls: list[int] = []
    worker_a = _counting_job(calls=a_calls, lock=spec)
    worker_b = _counting_job(calls=b_calls, lock=spec)

    worker_a.run_once()  # A wins, writes the marker, releases the lock
    # Hours later B ticks; the lock has long expired but the marker persists.
    worker_b.run_once()

    assert a_calls == [1]
    assert b_calls == []  # B sees the marker — no duplicate emit


def test_redis_disabled_runs_unguarded(fake_redis: _FakeRedis) -> None:
    """No Redis (local dev): bypass the lock and rely on in-process idempotency."""
    fake_redis.enabled = False
    spec = _window_spec()
    calls: list[int] = []
    job = _counting_job(calls=calls, lock=spec)

    job.run_once()
    job.run_once()

    assert calls == [1, 1]
    assert fake_redis.locks == {}
    assert fake_redis.markers == {}  # lock/marker never touched


def test_lock_events_are_logged(fake_redis: _FakeRedis, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="backend.jobs._periodic"):
        _counting_job(calls=[], lock=_excl_spec()).run_once()  # acquire + release
        # Contend on a held key to force a skip.
        fake_redis.locks["lock:busy"] = "held"
        _counting_job(calls=[], lock=_excl_spec(key="lock:busy")).run_once()

    messages = [r.message for r in caplog.records if "lock_" in r.message]
    assert any("lock_acquired job_kind=test_job" in m for m in messages)
    assert any("lock_released job_kind=test_job" in m for m in messages)
    assert any("lock_skipped job_kind=test_job" in m for m in messages)
    assert all("worker_pid=" in m for m in messages)
