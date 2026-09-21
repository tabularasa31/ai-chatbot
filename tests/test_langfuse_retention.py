"""Daily Langfuse trace retention: the only thing that ever expires a trace on
our self-hosted OSS server, which has no retention window of its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from backend.core.config import settings
from backend.jobs import langfuse_retention
from backend.observability import langfuse_purge


def _stub_client(monkeypatch: pytest.MonkeyPatch, pages: dict[int, list[str]]):
    listed: list[dict[str, Any]] = []
    deleted: list[list[str]] = []

    class _Traces:
        async def list(self, *, page: int, limit: int, **filters):
            listed.append({"page": page, **filters})
            return SimpleNamespace(
                data=[SimpleNamespace(id=i) for i in pages.get(page, [])]
            )

        async def delete_multiple(self, *, trace_ids):
            deleted.append(list(trace_ids))

    monkeypatch.setattr(langfuse_purge, "langfuse_purge_configured", lambda: True)
    monkeypatch.setattr(
        langfuse_purge, "_build_client", lambda: SimpleNamespace(trace=_Traces())
    )
    return listed, deleted


@pytest.mark.asyncio
async def test_retention_lists_by_cutoff_then_deletes_in_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(langfuse_purge, "_PAGE_SIZE", 2)
    monkeypatch.setattr(langfuse_purge, "_DELETE_BATCH", 3)
    listed, deleted = _stub_client(
        monkeypatch, {1: ["t1", "t2"], 2: ["t3", "t4"], 3: ["t5"]}
    )
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)

    count = await langfuse_purge.delete_traces_older_than(cutoff)

    assert count == 5
    assert [entry["page"] for entry in listed] == [1, 2, 3]
    assert all(entry["to_timestamp"] == cutoff for entry in listed)
    assert all("tags" not in entry for entry in listed)
    assert deleted == [["t1", "t2", "t3"], ["t4", "t5"]]


@pytest.mark.asyncio
async def test_retention_deletes_what_it_collected_when_backlog_exceeds_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike a workspace purge, a partial run is fine: whatever is left is
    still past the cutoff on the next nightly run."""
    monkeypatch.setattr(langfuse_purge, "_PAGE_SIZE", 1)
    monkeypatch.setattr(langfuse_purge, "_RETENTION_MAX_PAGES", 2)
    _, deleted = _stub_client(monkeypatch, {1: ["t1"], 2: ["t2"], 3: ["t3"]})

    count = await langfuse_purge.delete_traces_older_than(datetime.now(UTC))

    assert count == 2
    assert deleted == [["t1", "t2"]]


@pytest.mark.asyncio
async def test_retention_is_a_no_op_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(langfuse_purge, "langfuse_purge_configured", lambda: False)

    def explode():
        raise AssertionError("must not build a client with no credentials")

    monkeypatch.setattr(langfuse_purge, "_build_client", explode)
    assert await langfuse_purge.delete_traces_older_than(datetime.now(UTC)) == 0


@pytest.mark.asyncio
async def test_workspace_purge_still_filters_by_tenant_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared paging helper must not leak the retention filter into the
    workspace purge or vice versa."""
    listed, deleted = _stub_client(monkeypatch, {1: ["t1"]})

    count = await langfuse_purge.delete_traces_for_tenant("abc")

    assert count == 1
    assert listed == [{"page": 1, "tags": "tenant:abc"}]
    assert deleted == [["t1"]]


def test_cutoff_is_retention_days_before_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langfuse_trace_retention_days", 45)
    now = datetime(2026, 9, 21, 3, 17, tzinfo=UTC)
    assert langfuse_retention.retention_cutoff(now) == now - timedelta(days=45)


@pytest.mark.asyncio
async def test_cron_tick_deletes_past_cutoff_and_sends_ok_checkin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.observability as observability_module

    checkins: list[dict[str, Any]] = []

    def _fake_checkin(*, monitor_slug, status, check_in_id=None, **kwargs):
        checkins.append(
            {"slug": monitor_slug, "status": status, "check_in_id": check_in_id}
        )
        return "cid-1" if status == "in_progress" else check_in_id

    monkeypatch.setattr(observability_module, "capture_cron_checkin", _fake_checkin)
    monkeypatch.setattr(settings, "langfuse_trace_retention_days", 60)
    cutoffs: list[datetime] = []

    async def _fake_delete(cutoff: datetime) -> int:
        cutoffs.append(cutoff)
        return 7

    monkeypatch.setattr(langfuse_retention, "delete_traces_older_than", _fake_delete)

    await langfuse_retention._tick_langfuse_retention({})

    assert [c["status"] for c in checkins] == ["in_progress", "ok"]
    assert {c["slug"] for c in checkins} == {"langfuse-trace-retention"}
    assert checkins[1]["check_in_id"] == "cid-1"
    assert len(cutoffs) == 1
    assert cutoffs[0].tzinfo is not None
    assert abs((datetime.now(UTC) - cutoffs[0]) - timedelta(days=60)) < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_cron_tick_sends_error_checkin_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.observability as observability_module

    statuses: list[str] = []

    def _fake_checkin(*, monitor_slug, status, check_in_id=None, **kwargs):
        statuses.append(status)
        return "cid-1" if status == "in_progress" else check_in_id

    monkeypatch.setattr(observability_module, "capture_cron_checkin", _fake_checkin)

    async def _boom(cutoff: datetime) -> int:
        raise RuntimeError("langfuse down")

    monkeypatch.setattr(langfuse_retention, "delete_traces_older_than", _boom)

    with pytest.raises(RuntimeError, match="langfuse down"):
        await langfuse_retention._tick_langfuse_retention({})

    assert statuses == ["in_progress", "error"]


def test_retention_cron_is_registered_daily() -> None:
    from backend.core.queue import _CRON_JOBS

    job = langfuse_retention.langfuse_retention_cron
    assert job in _CRON_JOBS
    assert job.hour == {3} and job.minute == {17}
    assert langfuse_retention._CRON_MONITOR_CONFIG["schedule"]["value"] == "17 3 * * *"


def test_retention_window_has_langfuse_floor() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        type(settings)(LANGFUSE_TRACE_RETENTION_DAYS=2)
