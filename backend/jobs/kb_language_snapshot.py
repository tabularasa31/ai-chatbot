"""Daily background job: emit tenant_kb_language_snapshot events to PostHog.

Runs once per calendar day (UTC). Idempotent within a day via an in-memory
date guard — single-process safe (one Railway dyno).
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models import Document, Tenant
from backend.observability.metrics import capture_event
from backend.search.service import CYRILLIC_LANGUAGE_PREFIXES, LATIN_LANGUAGE_PREFIXES

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()
_last_run_lock = threading.Lock()
_last_run_date: date | None = None
_snapshot_thread: threading.Thread | None = None

_STARTUP_DELAY_SECONDS = 60
_CHECK_INTERVAL_SECONDS = 3600


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


def _run_snapshot_loop() -> None:
    _shutdown_event.wait(_STARTUP_DELAY_SECONDS)
    while not _shutdown_event.is_set():
        if _should_run_today():
            from backend.core.db import SessionLocal

            db = SessionLocal()
            try:
                count = run_kb_language_snapshot_for_all_tenants(db)
                _mark_run_today()
                logger.info("kb_language_snapshot: emitted events for %d tenants", count)
            except Exception:
                logger.exception("kb_language_snapshot loop error")
            finally:
                db.close()
        _shutdown_event.wait(_CHECK_INTERVAL_SECONDS)


def start_kb_snapshot_daily_thread() -> None:
    global _snapshot_thread
    if _snapshot_thread is not None:
        return
    t = threading.Thread(
        target=_run_snapshot_loop,
        daemon=True,
        name="kb-language-snapshot",
    )
    _snapshot_thread = t
    t.start()


def shutdown_kb_snapshot_thread() -> None:
    _shutdown_event.set()
    if _snapshot_thread is not None:
        _snapshot_thread.join(timeout=5)
