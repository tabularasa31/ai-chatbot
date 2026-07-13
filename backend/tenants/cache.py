"""Per-process TTL cache for near-static Tenant / TenantProfile rows.

``_ensure_chat_async`` loads ``Tenant`` and ``TenantProfile`` on every chat
turn (two separate DB round-trips inside ``chat_setup_ms``). Both rows are
near-static — tenant settings and the extracted knowledge profile change only
on an explicit admin action or a background extraction pass — yet the hot chat
path pays for them on every message.

This module collapses those two reads into an in-memory hit, bounded by an LRU
and a short TTL. Correctness is kept two ways:

* **TTL backstop** — an entry older than ``_TTL_SECONDS`` is treated as absent,
  so any missed invalidation self-heals within the window.
* **Explicit invalidation** — the tenant- and profile-update code paths call
  :func:`invalidate_tenant` right after they commit, so an admin edit takes
  effect immediately rather than after the TTL.

Cached values are *detached clones* decoupled from any :class:`Session`: only
mapped columns are copied (no relationships), so a cache hit can never trigger
a lazy load, and mutations made to a session-bound copy never leak back into
the cache. Callers must treat the returned instance as read-only.

The cache is intentionally not wired to a config flag — it is a pure latency
optimisation with a fail-safe TTL, not a behavioural toggle.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import TypeVar

from sqlalchemy.orm import class_mapper, make_transient_to_detached

from backend.models import Tenant, TenantProfile
from backend.models.base import Base

# Rows are near-static; 30s collapses the repeated per-turn reads of a busy
# session while bounding staleness for edits that slip past an invalidation
# site (or the deliberately un-invalidated LLM-alert state writes).
_TTL_SECONDS = 30.0

# Upper bound on distinct tenants held per cache. Far above any realistic
# concurrent-tenant count; exists only to cap memory under pathological churn.
_MAX_ENTRIES = 4096

_T = TypeVar("_T", bound=Base)


class _TtlLruCache:
    """Minimal thread-safe TTL + LRU map of ``tenant_id -> detached clone``."""

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        # Insertion-ordered; most-recently-used moved to the end.
        self._store: OrderedDict[str, tuple[float, Base]] = OrderedDict()

    def get(self, key: str) -> Base | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                # Expired: drop it so the next miss reloads.
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: Base) -> None:
        expires_at = time.monotonic() + self._ttl
        with self._lock:
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def pop(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_tenant_cache = _TtlLruCache(ttl_seconds=_TTL_SECONDS, max_entries=_MAX_ENTRIES)
_profile_cache = _TtlLruCache(ttl_seconds=_TTL_SECONDS, max_entries=_MAX_ENTRIES)


def _detached_clone(instance: _T) -> _T:
    """Copy an instance's mapped columns into a fresh, *detached* instance.

    Only column attributes are copied — relationships are skipped, so the clone
    never lazy-loads. Called while ``instance`` is still session-bound and fully
    loaded, so no attribute access here triggers a DB round-trip.

    The clone is promoted from transient to *detached* (via
    :func:`make_transient_to_detached`) so it carries a persistent identity key.
    That is what lets the chat path re-bind it with ``merge(load=False)`` —
    merge rejects transient (unpersisted) objects under ``load=False``.
    """
    model = type(instance)
    clone = model()
    for attr in class_mapper(model).column_attrs:
        setattr(clone, attr.key, getattr(instance, attr.key))
    make_transient_to_detached(clone)
    return clone


def _key(tenant_id: uuid.UUID | str) -> str:
    return str(tenant_id)


def get_cached_tenant(tenant_id: uuid.UUID | str) -> Tenant | None:
    """Return a detached ``Tenant`` clone for *tenant_id*, or ``None`` on miss.

    The returned instance is not attached to any session; callers that need a
    session-bound copy should ``merge(load=False)`` it. Treat as read-only.
    """
    cached = _tenant_cache.get(_key(tenant_id))
    return cached  # type: ignore[return-value]


def set_cached_tenant(tenant: Tenant) -> None:
    """Cache a detached clone of *tenant* keyed on its id."""
    _tenant_cache.set(_key(tenant.id), _detached_clone(tenant))


def get_cached_tenant_profile(tenant_id: uuid.UUID | str) -> TenantProfile | None:
    """Return a detached ``TenantProfile`` clone, or ``None`` on miss.

    Absence of a profile row is *not* cached — a bare miss always reloads — so
    a profile created after the first lookup is picked up on the next turn.
    """
    cached = _profile_cache.get(_key(tenant_id))
    return cached  # type: ignore[return-value]


def set_cached_tenant_profile(profile: TenantProfile) -> None:
    """Cache a detached clone of *profile* keyed on its tenant id."""
    _profile_cache.set(_key(profile.tenant_id), _detached_clone(profile))


def invalidate_tenant(tenant_id: uuid.UUID | str) -> None:
    """Drop both the tenant and profile cache entries for *tenant_id*.

    Call right after committing any change to a tenant's settings/key or its
    knowledge profile so the chat path stops serving the stale snapshot.
    """
    key = _key(tenant_id)
    _tenant_cache.pop(key)
    _profile_cache.pop(key)


def clear_cache() -> None:
    """Flush both caches. Intended for test isolation."""
    _tenant_cache.clear()
    _profile_cache.clear()
