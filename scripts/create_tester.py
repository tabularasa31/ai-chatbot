#!/usr/bin/env python3
"""Create an internal QA tester (plain password, MVP)."""

from __future__ import annotations

import argparse
import sys

from backend.core.db import SessionLocal
from backend.models import Tester


def main() -> int:
    parser = argparse.ArgumentParser(description="Create internal eval tester")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        print("error: username must be non-empty", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        existing = db.query(Tester).filter(Tester.username == username).first()
        if existing:
            print(f"error: tester with username {username!r} already exists", file=sys.stderr)
            return 1
        tester = Tester(username=username, password=args.password, is_active=True)
        db.add(tester)
        db.commit()
    finally:
        db.close()

    print(f"Created tester {username!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
