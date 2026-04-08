"""Delete old PII audit events according to a retention window."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from backend.core.db import SessionLocal
from backend.models import PiiEvent


def run(retention_days: int) -> int:
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    db = SessionLocal()
    try:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted_count = (
            db.query(PiiEvent)
            .filter(PiiEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(deleted_count or 0)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention-days", type=int, default=365)
    args = parser.parse_args()
    deleted = run(args.retention_days)
    print(f"Deleted {deleted} pii_events")
