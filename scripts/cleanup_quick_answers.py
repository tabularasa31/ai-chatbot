#!/usr/bin/env python3
"""Remove obviously unsafe quick answers created by older extractors."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.core.db import SessionLocal
from backend.documents.quick_answers import _TRIAL_MAX_LEN, _is_acceptable_support_email
from backend.models import QuickAnswer

logger = logging.getLogger(__name__)
_ROOT_FALLBACK_METHOD = "root" "_fallback"


def _should_delete(answer: QuickAnswer) -> str | None:
    metadata = answer.metadata_json if isinstance(answer.metadata_json, dict) else {}
    method = str(metadata.get("method") or "").strip().lower()

    if method == _ROOT_FALLBACK_METHOD:
        return _ROOT_FALLBACK_METHOD
    if answer.key == "support_email" and not _is_acceptable_support_email(
        answer.value,
        page_url=answer.source_url,
    ):
        return "support_email_invalid"
    if answer.key == "trial_info" and len(answer.value) > _TRIAL_MAX_LEN:
        return "trial_info_too_long"
    return None


def run_cleanup(
    *,
    dry_run: bool,
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    db = session_factory()
    removed = 0
    try:
        answers = db.query(QuickAnswer).all()
        for answer in answers:
            reason = _should_delete(answer)
            if reason is None:
                continue
            removed += 1
            logger.info(
                "cleanup_quick_answers_delete",
                extra={
                    "quick_answer_id": str(answer.id),
                    "key": answer.key,
                    "reason": reason,
                    "source_url": answer.source_url,
                    "dry_run": dry_run,
                    "value_preview": answer.value[:80],
                },
            )
            if not dry_run:
                db.delete(answer)
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return removed
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete invalid historical quick answers")
    parser.add_argument("--dry-run", action="store_true", help="Report removals without deleting rows")
    args = parser.parse_args(argv)

    removed = run_cleanup(dry_run=args.dry_run)
    mode = "Would remove" if args.dry_run else "Removed"
    print(f"{mode} {removed} quick answer record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
