"""Workspace deletion: the owner's only exit, and what it reaches.

Covers the three halves that were missing around ``delete_tenant``: the route
being findable at all (it is in the OpenAPI schema now), the ordering that
keeps the local delete and the external cleanup from disagreeing, and the
purge job itself.

The delete of our own rows is covered by ``tests/test_clients.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from arq import Retry
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from backend.core import db as core_db
from backend.core.security import create_access_token
from backend.email import purge as email_purge
from backend.jobs import workspace_purge
from backend.observability import langfuse_purge
from backend.models import (
    Base,
    BackgroundJob,
    EscalationTicket,
    EscalationTrigger,
    Tenant,
    User,
)
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
        # The whole design rests on this happening while the workspace is still
        # there. Asserting only that the enqueue happened would pass just as
        # well against an implementation that scheduled the cleanup *after* the
        # commit — which is the bug the ordering exists to prevent.
        scheduled["tenant_present_at_enqueue"] = (
            db_session.query(Tenant).filter(Tenant.id == tenant_id).first() is not None
        )
        return "job-1"

    monkeypatch.setattr(workspace_purge, "external_purge_needed", lambda: True)
    monkeypatch.setattr(workspace_purge, "enqueue_workspace_purge_sync", fake_enqueue)

    response = tenant.delete(
        f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 204

    assert scheduled["tenant_present_at_enqueue"] is True
    assert str(scheduled["tenant_id"]) == tenant_id
    assert "leaving@acme.com" in [e.lower() for e in scheduled["emails"]]

    # ...and gone afterwards, so the assertion above was about ordering rather
    # than about the delete never having happened.
    db_session.expire_all()
    assert (
        db_session.query(Tenant).filter(Tenant.id == uuid.UUID(tenant_id)).first()
        is None
    )


def test_delete_skips_cleanup_when_nothing_is_configured(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no Langfuse and no Brevo there is nothing out there to purge, so a
    deletion must not be blocked on scheduling a job with no work."""
    token = register_and_verify_user(tenant, db_session, email="local@acme.com")
    tenant_id = _create_workspace(client=tenant, token=token)

    # State the precondition rather than inheriting it: conftest pops the
    # Langfuse and Brevo env vars, and this test is meaningless if that stops
    # being true.
    assert workspace_purge.external_purge_needed() is False

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


async def _queue_job(db: AsyncSession, job_id: str, addresses: list[str]) -> None:
    """Write the status row the job reads its addresses back out of."""
    db.add(
        BackgroundJob(
            arq_job_id=job_id,
            kind="purge_workspace_external_data",
            tenant_id=None,
            payload={"addresses": addresses},
            status="queued",
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_purge_retries_while_the_workspace_still_exists(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete that scheduled this may not have committed, or may have
    failed. Erring towards leaving external data behind for a live workspace
    beats destroying the conversations of one its owner still has — and it must
    raise rather than return, or a cleanup that never happened is recorded as
    one that did."""
    tenant_id = uuid.uuid4()
    purge_db.add(Tenant(id=tenant_id, name="Still Here"))
    await _queue_job(purge_db, "job-present", ["someone@acme.com"])

    langfuse = AsyncMock()
    brevo = AsyncMock()
    monkeypatch.setattr(workspace_purge, "delete_traces_for_tenant", langfuse)
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    with pytest.raises(Retry):
        await workspace_purge.purge_workspace_external_data(
            {"job_id": "job-present"}, str(tenant_id)
        )

    langfuse.assert_not_awaited()
    brevo.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_reads_addresses_from_the_payload_not_its_arguments(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arq logs every job's arguments at INFO on each start, so the addresses
    travel in ``background_jobs.payload`` instead — which also keeps them
    recoverable after arq's one-hour result TTL."""
    await _queue_job(purge_db, "job-payload", ["departed@acme.com"])

    brevo = AsyncMock(return_value=1)
    monkeypatch.setattr(workspace_purge, "delete_traces_for_tenant", AsyncMock())
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    await workspace_purge.purge_workspace_external_data(
        {"job_id": "job-payload"}, str(uuid.uuid4())
    )

    brevo.assert_awaited_once_with(["departed@acme.com"])


@pytest.mark.asyncio
async def test_purge_keeps_addresses_a_surviving_workspace_still_uses(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brevo contacts are account-wide, and deleting one discards its
    unsubscribe state. Every way an address can still be ours is a way it must
    survive: a live account, a ticket on a workspace that is staying, and
    another workspace's configured support inbox — which appears in no user row
    and no ticket at all, the exact shape of an agency's shared inbox."""
    gone = uuid.uuid4()
    staying = uuid.uuid4()
    purge_db.add(
        Tenant(
            id=staying,
            name="Staying",
            settings={"support": {"l2_email": "desk@agency.com"}},
        )
    )
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
    await _queue_job(
        purge_db,
        "job-shared",
        [
            "departed@acme.com",
            "shared@example.com",
            "alsoshared@example.com",
            "desk@agency.com",
        ],
    )

    langfuse = AsyncMock(return_value=3)
    brevo = AsyncMock(return_value=1)
    monkeypatch.setattr(workspace_purge, "delete_traces_for_tenant", langfuse)
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    await workspace_purge.purge_workspace_external_data(
        {"job_id": "job-shared"}, str(gone)
    )

    langfuse.assert_awaited_once_with(str(gone))
    brevo.assert_awaited_once_with(["departed@acme.com"])


@pytest.mark.asyncio
async def test_purge_matches_addresses_case_insensitively(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing normalises an address on the way in — registration stores what
    was typed and a widget supplies ticket addresses verbatim — so the same
    mailbox reaches us in two casings. To Brevo they are one contact, so a
    case-sensitive guard would delete one we still hold."""
    staying = uuid.uuid4()
    purge_db.add(Tenant(id=staying, name="Staying"))
    purge_db.add(
        User(
            id=uuid.uuid4(),
            email="foo@example.com",
            password_hash="x",
            tenant_id=staying,
        )
    )
    await _queue_job(purge_db, "job-case", ["Foo@Example.com", "gone@acme.com"])

    brevo = AsyncMock(return_value=1)
    monkeypatch.setattr(workspace_purge, "delete_traces_for_tenant", AsyncMock())
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    await workspace_purge.purge_workspace_external_data(
        {"job_id": "job-case"}, str(uuid.uuid4())
    )

    brevo.assert_awaited_once_with(["gone@acme.com"])


@pytest.mark.asyncio
async def test_purge_retries_when_a_vendor_fails(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken vendor must not hold the other's data hostage for the length
    of the backoff — both are attempted, then the job raises ``Retry``, which
    is the only exception arq actually re-queues."""
    await _queue_job(purge_db, "job-vendor", ["departed@acme.com"])

    monkeypatch.setattr(
        workspace_purge,
        "delete_traces_for_tenant",
        AsyncMock(side_effect=RuntimeError("langfuse down")),
    )
    brevo = AsyncMock(return_value=1)
    monkeypatch.setattr(workspace_purge, "delete_contacts", brevo)

    with pytest.raises(Retry):
        await workspace_purge.purge_workspace_external_data(
            {"job_id": "job-vendor"}, str(uuid.uuid4())
        )

    brevo.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_failure_carries_no_address_into_its_error(
    purge_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``register_job`` writes ``str(exc)`` into ``background_jobs.last_error``
    and arq logs it at ERROR, which Sentry captures. A vendor client's message
    can quote the request, and the request is an address."""
    await _queue_job(purge_db, "job-pii", ["visitor@example.com"])

    monkeypatch.setattr(
        workspace_purge,
        "delete_traces_for_tenant",
        AsyncMock(side_effect=RuntimeError("failed for visitor@example.com")),
    )
    monkeypatch.setattr(workspace_purge, "delete_contacts", AsyncMock(return_value=0))

    with pytest.raises(Retry) as raised:
        await workspace_purge.purge_workspace_external_data(
            {"job_id": "job-pii"}, str(uuid.uuid4())
        )

    assert "visitor@example.com" not in repr(raised.value)


@pytest.mark.asyncio
async def test_reference_check_failure_does_not_leak_bound_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database error inside the reference check carries the addresses as
    bound parameters in its message, and that message is persisted and shipped
    to Sentry. Only the exception's type name may escape."""
    import sqlalchemy.exc

    class _Boom:
        async def __aenter__(self):
            raise sqlalchemy.exc.StatementError(
                message="db is gone",
                statement="SELECT users.email FROM users WHERE users.email IN (?)",
                params=("visitor@example.com",),
                orig=Exception("db is gone"),
            )

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(core_db, "AsyncSessionLocal", lambda: _Boom())

    with pytest.raises(RuntimeError) as raised:
        await workspace_purge._addresses_still_referenced(
            ["visitor@example.com"], purged_tenant_id=str(uuid.uuid4())
        )

    assert "visitor@example.com" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_enqueue_keeps_the_status_row_off_the_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``background_jobs.tenant_id`` is ON DELETE CASCADE to ``tenants``, so
    stamping it would have the local delete cascade away the row carrying the
    addresses, seconds after it was written. The addresses go in the payload
    rather than the arguments, which arq logs; and the job is deferred so it
    cannot run before the delete it cleans up after has committed."""
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
    assert captured["args"] == (str(tenant_id),)
    assert "departed@acme.com" not in str(captured["args"])
    assert captured["kwargs"]["payload"]["addresses"] == ["departed@acme.com"]
    assert captured["kwargs"].get("tenant_id") is None
    assert captured["kwargs"]["_defer_by"] > 0


# ── The vendor purges ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_langfuse_purge_pages_forward_then_deletes_in_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect by paging forward, then delete — not "drain page 1 until empty".
    Langfuse processes a delete asynchronously, so a just-deleted trace can
    still be listed and a drain loop would never terminate."""
    monkeypatch.setattr(langfuse_purge, "langfuse_purge_configured", lambda: True)
    monkeypatch.setattr(langfuse_purge, "_PAGE_SIZE", 2)
    monkeypatch.setattr(langfuse_purge, "_DELETE_BATCH", 3)

    pages = {1: ["t1", "t2"], 2: ["t3", "t4"], 3: ["t5"]}
    listed: list[int] = []
    deleted: list[list[str]] = []

    class _Traces:
        async def list(self, *, page: int, limit: int, tags: str):
            listed.append(page)
            assert tags == "tenant:abc"
            return SimpleNamespace(
                data=[SimpleNamespace(id=i) for i in pages.get(page, [])]
            )

        async def delete_multiple(self, *, trace_ids):
            deleted.append(list(trace_ids))
            return None

    client = SimpleNamespace(trace=_Traces())
    monkeypatch.setattr(langfuse_purge, "_build_client", lambda: client)

    count = await langfuse_purge.delete_traces_for_tenant("abc")

    assert count == 5
    assert listed == [1, 2, 3]
    assert deleted == [["t1", "t2", "t3"], ["t4", "t5"]]


@pytest.mark.asyncio
async def test_langfuse_purge_fails_rather_than_half_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hitting the page cap and deleting only what was collected would be a
    partial purge recorded as a complete one."""
    monkeypatch.setattr(langfuse_purge, "langfuse_purge_configured", lambda: True)
    monkeypatch.setattr(langfuse_purge, "_PAGE_SIZE", 1)
    monkeypatch.setattr(langfuse_purge, "_MAX_PAGES", 2)

    deleted: list[list[str]] = []

    class _Traces:
        async def list(self, *, page: int, limit: int, tags: str):
            return SimpleNamespace(data=[SimpleNamespace(id=f"t{page}")])

        async def delete_multiple(self, *, trace_ids):
            deleted.append(list(trace_ids))

    monkeypatch.setattr(
        langfuse_purge, "_build_client", lambda: SimpleNamespace(trace=_Traces())
    )

    with pytest.raises(RuntimeError):
        await langfuse_purge.delete_traces_for_tenant("abc")

    assert deleted == []


@pytest.mark.asyncio
async def test_langfuse_purge_is_a_no_op_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode():
        raise AssertionError("must not build a client with no credentials")

    monkeypatch.setattr(langfuse_purge, "_build_client", explode)
    assert await langfuse_purge.delete_traces_for_tenant("abc") == 0


@pytest.mark.asyncio
async def test_brevo_purge_treats_404_as_already_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 is the state we are asking for, not a failure — and it is the normal
    answer for a visitor address, which only ever rode out as ``replyTo``."""
    monkeypatch.setattr(email_purge, "brevo_purge_configured", lambda: True)
    monkeypatch.setattr(email_purge.settings, "BREVO_API_KEY", "key", raising=False)

    requested: list[str] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def delete(self, url, headers=None):
            requested.append(url)
            return SimpleNamespace(status_code=404 if "ghost" in url else 204)

    monkeypatch.setattr(email_purge.httpx, "AsyncClient", lambda **_k: _Client())

    deleted = await email_purge.delete_contacts(["real@acme.com", "ghost@acme.com"])

    assert deleted == 1
    assert len(requested) == 2


@pytest.mark.asyncio
async def test_brevo_purge_error_names_no_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure has to be reportable without putting the very PII this job
    exists to remove into ``background_jobs.last_error``."""
    monkeypatch.setattr(email_purge, "brevo_purge_configured", lambda: True)
    monkeypatch.setattr(email_purge.settings, "BREVO_API_KEY", "key", raising=False)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def delete(self, url, headers=None):
            return SimpleNamespace(status_code=500)

    monkeypatch.setattr(email_purge.httpx, "AsyncClient", lambda **_k: _Client())

    with pytest.raises(RuntimeError) as raised:
        await email_purge.delete_contacts(["visitor@example.com"])

    assert "visitor@example.com" not in str(raised.value)
    assert "500" in str(raised.value)


@pytest.mark.asyncio
async def test_brevo_purge_is_a_no_op_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(email_purge.settings, "BREVO_API_KEY", None, raising=False)
    assert await email_purge.delete_contacts(["someone@acme.com"]) == 0


# ── Who may do it ────────────────────────────────────────────────────────────


def test_an_operator_cannot_delete_the_workspace(
    tenant: TestClient, db_session: Session
) -> None:
    """Only the owner leaves. An operator is a member of the workspace, so the
    wrong-workspace 404 does not cover them — this is the 403."""
    token = register_and_verify_user(tenant, db_session, email="boss@acme.com")
    tenant_id = _create_workspace(client=tenant, token=token)

    operator = User(
        id=uuid.uuid4(),
        email="ops@acme.com",
        password_hash="x",
        tenant_id=uuid.UUID(tenant_id),
        is_verified=True,
        role="operator",
    )
    db_session.add(operator)
    db_session.commit()

    op_token = create_access_token(
        data={"sub": str(operator.id), "email": operator.email}
    )
    refused = tenant.delete(
        f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {op_token}"}
    )
    assert refused.status_code == 403

    # And the workspace is untouched by the attempt.
    assert (
        tenant.get("/tenants/me", headers={"Authorization": f"Bearer {token}"})
        .json()["id"]
        == tenant_id
    )
