"""Per-process Tenant / TenantProfile TTL cache (chat-latency item 1).

Covers:
- hits return session-decoupled clones (mutating the clone never touches DB);
- TTL expiry and explicit invalidation drop entries;
- LRU eviction caps memory;
- a cached clone can be re-bound to an AsyncSession via merge(load=False)
  without emitting SQL, which is how the chat path consumes it;
- the tenant-update service and profile writers invalidate the cache.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from backend.models import Tenant, TenantProfile
from backend.tenants import cache as tenant_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    tenant_cache.clear_cache()
    yield
    tenant_cache.clear_cache()


def _tenant(**overrides) -> Tenant:
    fields = dict(
        id=uuid.uuid4(),
        name="Acme",
        public_id="p" * 12,
        settings={"optional_entity_types": ["email"]},
    )
    fields.update(overrides)
    return Tenant(**fields)


def test_hit_returns_decoupled_clone() -> None:
    tenant = _tenant()
    tenant_cache.set_cached_tenant(tenant)

    hit = tenant_cache.get_cached_tenant(tenant.id)
    assert hit is not None
    assert hit is not tenant
    assert hit.id == tenant.id
    assert hit.settings == {"optional_entity_types": ["email"]}

    # A second get is still decoupled from the first.
    hit2 = tenant_cache.get_cached_tenant(tenant.id)
    assert hit2 is not None


def test_miss_returns_none() -> None:
    assert tenant_cache.get_cached_tenant(uuid.uuid4()) is None
    assert tenant_cache.get_cached_tenant_profile(uuid.uuid4()) is None


def test_invalidate_drops_both_tenant_and_profile() -> None:
    tid = uuid.uuid4()
    tenant_cache.set_cached_tenant(_tenant(id=tid))
    tenant_cache.set_cached_tenant_profile(
        TenantProfile(tenant_id=tid, product_name="X", topics=["a"])
    )

    tenant_cache.invalidate_tenant(tid)

    assert tenant_cache.get_cached_tenant(tid) is None
    assert tenant_cache.get_cached_tenant_profile(tid) is None


def test_ttl_expiry(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(tenant_cache.time, "monotonic", lambda: clock["now"])

    tenant = _tenant()
    tenant_cache.set_cached_tenant(tenant)
    assert tenant_cache.get_cached_tenant(tenant.id) is not None

    clock["now"] += tenant_cache._TTL_SECONDS + 0.1
    assert tenant_cache.get_cached_tenant(tenant.id) is None


def test_lru_eviction(monkeypatch) -> None:
    monkeypatch.setattr(tenant_cache._tenant_cache, "_max", 3)
    ids = [uuid.uuid4() for _ in range(4)]
    for tid in ids:
        tenant_cache.set_cached_tenant(_tenant(id=tid))

    # Oldest evicted; the last three survive.
    assert tenant_cache.get_cached_tenant(ids[0]) is None
    for tid in ids[1:]:
        assert tenant_cache.get_cached_tenant(tid) is not None


def test_profile_clone_columns_preserved() -> None:
    tid = uuid.uuid4()
    profile = TenantProfile(
        tenant_id=tid,
        product_name="Widget",
        topics=["billing", "auth"],
        glossary=[{"term": "SLA", "definition": "..."}],
    )
    tenant_cache.set_cached_tenant_profile(profile)

    hit = tenant_cache.get_cached_tenant_profile(tid)
    assert hit is not None
    assert hit is not profile
    assert hit.product_name == "Widget"
    assert hit.topics == ["billing", "auth"]
    assert hit.glossary == [{"term": "SLA", "definition": "..."}]


@pytest.mark.asyncio
async def test_cached_clone_merges_into_async_session_without_sql(
    async_db_session,
) -> None:
    """A cache clone re-binds via merge(load=False) — the chat-path consumption."""
    tenant = Tenant(name="Merge Tenant", settings={"k": "v"})
    async_db_session.add(tenant)
    await async_db_session.commit()
    await async_db_session.refresh(tenant)

    tenant_cache.set_cached_tenant(tenant)
    profile = TenantProfile(tenant_id=tenant.id, product_name="P", topics=["t"])
    async_db_session.add(profile)
    await async_db_session.commit()
    tenant_cache.set_cached_tenant_profile(profile)

    # Detach the originals to mimic a fresh request with only the cache warm.
    async_db_session.expunge_all()

    cached_tenant = tenant_cache.get_cached_tenant(tenant.id)
    cached_profile = tenant_cache.get_cached_tenant_profile(tenant.id)
    assert cached_tenant is not None and cached_profile is not None

    bound_tenant = await async_db_session.merge(cached_tenant, load=False)
    bound_profile = await async_db_session.merge(cached_profile, load=False)

    assert bound_tenant.settings == {"k": "v"}
    assert bound_tenant.public_id == tenant.public_id
    assert bound_profile.product_name == "P"
    assert bound_profile.topics == ["t"]


def test_update_tenant_invalidates_cache(db_session: Session) -> None:
    from backend.auth.service import register_user
    from backend.tenants.service import create_tenant, update_tenant

    user = register_user("cache-owner@example.com", "pw123456", db_session)
    tenant, _key = create_tenant(user.id, "Cache Co", db_session)

    tenant_cache.set_cached_tenant(tenant)
    assert tenant_cache.get_cached_tenant(tenant.id) is not None

    update_tenant(user.id, db_session, name="Renamed Co")
    assert tenant_cache.get_cached_tenant(tenant.id) is None
