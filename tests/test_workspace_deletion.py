"""Workspace deletion: the owner's only exit, and what it reaches.

Covers the three halves that were missing around ``delete_tenant``: the route
being findable at all (it is in the OpenAPI schema now), the ordering that
keeps the local delete and the external cleanup from disagreeing, and the
purge job itself.

The delete of our own rows is covered by ``tests/test_clients.py``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from backend.core import db as core_db
from backend.jobs import workspace_purge
from backend.models import EscalationTicket, Tenant, User
from backend.models.base import Base
from backend.models.enums import EscalationTrigger
from backend.tenants.service import collect_external_addresses
from tests.conftest import register_and_verify_user


def _create_workspace(client: TestClient, token: str, name: str = "Acme") -> str:
    response = client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()["id"]


# ── The route is findable ────────────────────────────────────────────────────


def test_delete_route_is_documented(tenant: TestClient) -> None:
    """Deleting your own workspace is a legitimate API action, so it is in the
    schema. Hiding it made the exit harder to find without making it harder to
    perform."""
    schema = tenant.get("/openapi.json").json()
    assert "delete" in schema["paths"]["/tenants/{tenant_id}"]


# ── What the purge is handed ─────────────────────────────────────────────────


def test_collect_addresses_covers_members_inbox_and_visitors(
    tenant: TestClient, db_session: Session
) -> None:
    """Everything the workspace's existence put into Brevo, deduplicated and
    case-insensitively so."""
    token = register_and_verify_user(tenant, db_session, email="owner@acme.com")
    tenant_id = uuid.UUID(_create_workspace(client=tenant, token=token))

    tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "support@acme.com"},
    )
    db_session.add(
        EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-1",
            primary_question="where are my invoices",
            trigger=EscalationTrigger.low_similarity,
            user_email="visitor@example.com",
        )
    )
    # Same visitor, second ticket, different casing — one contact in Brevo.
    db_session.add(
        EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-2",
            primary_question="still nothing",
            trigger=EscalationTrigger.low_similarity,
            user_email="Visitor@example.com",
        )
    )
    db_session.commit()

    addresses = collect_external_addresses(tenant_id, db_session)

    lowered = [a.lower() for a in addresses]
    assert "owner@acme.com" in lowered
    assert "support@acme.com" in lowered
    assert lowered.count("visitor@example.com") == 1


# ── Ordering: cleanup is scheduled before anything is destroyed ──────────────


def test_delete_refuses_when_cleanup_cannot_be_scheduled(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace deleted with no cleanup queued would leave conversations in
    Langfuse and no row left in our database naming them. So: 503, and nothing
    is deleted."""
    token = register_and_verify_user(tenant, db_session, email="stuck@acme.com")
    tenant_id = _create_workspace(client=tenant, token=token)

    monkeypatch.setattr(workspace_purge, "external_purge_needed", lambda: True)
    monkeypatch.setattr(
        workspace_purge,
        "enqueue_workspace_purge_sync",
        lambda **_kwargs: None,
    )

    response = tenant.delete(
        f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 503

    still_there = tenant.get(
        "/tenants/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert still_there.status_code == 200
    assert still_there.json()["id"] == tenant_id


def test_delete_schedules_cleanup_then_deletes(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job is handed the tenant id and the addresses, because after the
    delete there is no row left to read either from."""
    token = register_and_verify_user(tenant, db_session, email="leaving@acme.com")
    tenant_id = _create_workspace(client=tenant, token=token)

    scheduled: dict[str, Any] = {}

    def fake_enqueue(*, tenant_id: uuid.UUID, emails: list[str]) -> str:
        scheduled["tenant_id"] = tenant_id
        scheduled["emails"] = emails
        return "job-1"

    monkeypatch.setattr(workspace_purge, "external_purge_needed", lambda: True)
    monkeypatch.setattr(workspace_purge, "enqueue_workspace_purge_sync", fake_enqueue)

    response = tenant.delete(
        f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 204

    assert str(scheduled["tenant_id"]) == tenant_id
    assert "leaving@acme.com" in [e.lower() for e in scheduled["emails"]]


def test_delete_skips_cleanup_when_nothing_is_configured(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no Langfuse and no Brevo there is nothing out there to purge, so a
    deletion must not be blocked on scheduling a job with no work."""
    token = register_and_verify_user(tenant, db_session, email="local@acme.com")
    tenant_id = _create_workspace(client=tenant, token=token)

    def explode(**_kwargs: Any) -> str:
        raise AssertionError("cleanup must not be scheduled when unconfigured")

    monkeypatch.setattr(workspace_purge, "enqueue_workspace_purge_sync", explode)

    response = tenant.delete(
        f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 204


# ── The purge job ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def purge_db():
    """Async SQLite engine wired into ``core_db.AsyncSessionLocal``.

    The job opens its own sessions — it runs in the worker, long after the
    request that scheduled it — so the engine has to be swapped underneath it.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    original = core_db.AsyncSessionLocal
    core_db.AsyncSessionLocal = factory
    try:
        async with factory() as session:
            yield session
    finally:
        core_db.AsyncSessionLocal = original
        await engine.dispose()


@pytest.mark.asyncio
async def test_purge_aborts_while_the_workspace_still_exists(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete that scheduled this can fail after the enqueue. Erring
    towards leaving external data behind for a live workspace beats destroying
    the conversations of one its owner still has."""
    tenant_id = uuid.uuid4()
    purge_db.add(Tenant(id=tenant_id, name="Still Here"))
    await purge_db.commit()

    langfuse = AsyncMock()
    brevo = AsyncMock()
    monkeypatch.setattr(workspace_purge, "delete_traces_for_tenant", langfuse)
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    await workspace_purge.purge_workspace_external_data(
        {}, str(tenant_id), ["someone@acme.com"]
    )

    langfuse.assert_not_awaited()
    brevo.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_deletes_traces_and_unshared_addresses(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brevo contacts are account-wide, so an address we still hold for a
    workspace that is staying survives the purge of one that is leaving."""
    gone = uuid.uuid4()
    staying = uuid.uuid4()
    purge_db.add(Tenant(id=staying, name="Staying"))
    purge_db.add(
        User(
            id=uuid.uuid4(),
            email="shared@example.com",
            password_hash="x",
            tenant_id=staying,
        )
    )
    purge_db.add(
        EscalationTicket(
            tenant_id=staying,
            ticket_number="ESC-9",
            primary_question="q",
            trigger=EscalationTrigger.low_similarity,
            user_email="alsoshared@example.com",
        )
    )
    await purge_db.commit()

    langfuse = AsyncMock(return_value=3)
    brevo = AsyncMock(return_value=1)
    monkeypatch.setattr(workspace_purge, "delete_traces_for_tenant", langfuse)
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    await workspace_purge.purge_workspace_external_data(
        {},
        str(gone),
        ["departed@acme.com", "shared@example.com", "alsoshared@example.com"],
    )

    langfuse.assert_awaited_once_with(str(gone))
    brevo.assert_awaited_once_with(["departed@acme.com"])


@pytest.mark.asyncio
async def test_purge_raises_when_a_vendor_fails_so_arq_retries(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken vendor must not hold the other's data hostage for the length
    of the backoff — both are attempted, then the job fails so it is retried."""
    monkeypatch.setattr(
        workspace_purge,
        "delete_traces_for_tenant",
        AsyncMock(side_effect=RuntimeError("langfuse down")),
    )
    brevo = AsyncMock(return_value=1)
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    with pytest.raises(RuntimeError):
        await workspace_purge.purge_workspace_external_data(
            {}, str(uuid.uuid4()), ["departed@acme.com"]
        )

    brevo.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_keeps_the_status_row_off_the_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``background_jobs.tenant_id`` is ON DELETE CASCADE to ``tenants``, so
    stamping it would have the local delete cascade away the record of the
    outstanding cleanup seconds after it was written. The tenant id travels in
    the arguments and the payload instead, and the job is deferred so it cannot
    run before the delete it is cleaning up after has committed."""
    captured: dict[str, Any] = {}

    async def fake_enqueue(name: str, *args: Any, **kwargs: Any) -> str:
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "job-7"

    monkeypatch.setattr(workspace_purge, "enqueue", fake_enqueue)

    tenant_id = uuid.uuid4()
    job_id = await workspace_purge.enqueue_workspace_purge(
        tenant_id=tenant_id, emails=["departed@acme.com"]
    )

    assert job_id == "job-7"
    assert captured["args"] == (str(tenant_id), ["departed@acme.com"])
    assert captured["kwargs"]["payload"]["tenant_id"] == str(tenant_id)
    assert captured["kwargs"].get("tenant_id") is None
    assert captured["kwargs"]["_defer_by"] > 0
