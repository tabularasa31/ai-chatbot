"""Daily background job: emit tenant_kb_language_snapshot events to PostHog.

Runs once per calendar day (UTC). Idempotent within a process via an in-memory
date guard; across workers a Redis distributed lock (keyed on the UTC date)
ensures only one worker emits the snapshot per day. Without Redis (local dev)
the in-memory guard alone applies — single-process safe.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.jobs._periodic import LockSpec, PeriodicJob
from backend.models import Document, Tenant
from backend.observability.metrics import capture_event
from backend.search.service import CYRILLIC_LANGUAGE_PREFIXES, LATIN_LANGUAGE_PREFIXES

logger = logging.getLogger(__name__)

_last_run_lock = threading.Lock()
_last_run_date: date | None = None

_STARTUP_DELAY_SECONDS = 60
_CHECK_INTERVAL_SECONDS = 3600
# The lock only guards the run's duration (mutual exclusion). 10 min covers the
# all-tenants scan with ample buffer; a crashed holder self-heals well before
# the next hourly tick.
_LOCK_TTL_SECONDS = 600
# The durable "emitted today" marker is what makes the daily snapshot single-run
# across the cluster. Keyed on the UTC date, so a new day is a fresh key; 26h
# TTL is just to auto-clean stale keys (it outlasts the day comfortably).
_DONE_MARKER_TTL_SECONDS = 26 * 3600


def _lang_to_script(lang: str) -> str:
    lower = lang.strip().lower()
    if lower.startswith(CYRILLIC_LANGUAGE_PREFIXES):
        return "cyrillic"
    if lower.startswith(LATIN_LANGUAGE_PREFIXES):
        return "latin"
    return "other"


def _emit_snapshot_for_tenant(tenant: Tenant, db: Session) -> bool:
    rows = (
        db.query(Document.language, func.count().label("cnt"))
        .filter(Document.tenant_id == tenant.id)
        .group_by(Document.language)
        .all()
    )
    total_documents = sum(r.cnt for r in rows)
    if total_documents == 0:
        return False

    language_distribution: dict[str, int] = {}
    null_count = 0
    for lang, cnt in rows:
        if lang is None:
            null_count = cnt
        else:
            language_distribution[lang] = cnt

    documents_with_language = total_documents - null_count
    language_count = len(language_distribution)
    is_multilingual = language_count > 1
    dominant_language = (
        max(language_distribution, key=lambda k: language_distribution[k])
        if language_distribution
        else None
    )

    scripts: set[str] = set()
    for lang in language_distribution:
        s = _lang_to_script(lang)
        if s != "other":
            scripts.add(s)
    kb_scripts = sorted(scripts)

    tenant_created = tenant.created_at
    if tenant_created and tenant_created.tzinfo is None:
        tenant_created = tenant_created.replace(tzinfo=UTC)
    tenant_age_days = (datetime.now(UTC) - tenant_created).days if tenant_created else 0

    capture_event(
        "tenant_kb_language_snapshot",
        distinct_id=str(tenant.public_id),
        tenant_id=str(tenant.public_id),
        properties={
            "total_documents": total_documents,
            "documents_with_language": documents_with_language,
            "language_count": language_count,
            "is_multilingual": is_multilingual,
            "kb_scripts": kb_scripts,
            "script_count": len(kb_scripts),
            "dominant_language": dominant_language,
            "language_distribution": language_distribution,
            "tenant_age_days": tenant_age_days,
        },
    )
    return True


def run_kb_language_snapshot_for_all_tenants(db: Session) -> int:
    """Emit tenant_kb_language_snapshot for every active tenant that has documents.

    Returns the number of tenants for which an event was emitted.
    """
    tenants = db.query(Tenant).filter(Tenant.is_active == True).all()  # noqa: E712
    count = 0
    for tenant in tenants:
        try:
            if _emit_snapshot_for_tenant(tenant, db):
                count += 1
        except Exception:
            logger.exception("kb_language_snapshot failed for tenant %s", tenant.id)
    return count


def _should_run_today() -> bool:
    today = datetime.now(UTC).date()
    with _last_run_lock:
        return _last_run_date != today


def _mark_run_today() -> None:
    global _last_run_date
    today = datetime.now(UTC).date()
    with _last_run_lock:
        _last_run_date = today


def _run_snapshot_once() -> None:
    if not _should_run_today():
        return
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        count = run_kb_language_snapshot_for_all_tenants(db)
        _mark_run_today()
        logger.info("kb_language_snapshot: emitted events for %d tenants", count)
    finally:
        db.close()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _daily_lock_key() -> str:
    return f"lock:kb_snapshot:daily:{_today()}"


def _daily_done_marker() -> str:
    return f"done:kb_snapshot:daily:{_today()}"


_job = PeriodicJob(
    name="kb-language-snapshot",
    work=_run_snapshot_once,
    interval_seconds=_CHECK_INTERVAL_SECONDS,
    startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    lock=LockSpec(
        job_kind="kb_snapshot_daily",
        key_factory=_daily_lock_key,
        ttl_seconds=_LOCK_TTL_SECONDS,
        done_marker_factory=_daily_done_marker,
        done_ttl_seconds=_DONE_MARKER_TTL_SECONDS,
    ),
)


def start_kb_snapshot_daily_thread() -> None:
    _job.start()


def shutdown_kb_snapshot_thread() -> None:
    _job.shutdown()
