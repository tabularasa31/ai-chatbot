#!/usr/bin/env python3
"""
One-time script to backfill public_id for existing clients.
Run this AFTER deploying the Client model change (if migration doesn't do it).
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.db import SessionLocal
from backend.models import Client
from backend.core.utils import generate_public_id


def main() -> None:
    db = SessionLocal()
    try:
        # Find clients without public_id (for DBs where column was added nullable)
        clients = db.query(Client).filter(Client.public_id == None).all()
        if not clients:
            print("✅ All clients already have public_id")
            return

        print(f"Backfilling {len(clients)} clients...")
        for client in clients:
            client.public_id = generate_public_id()
            print(f"  {client.name} → {client.public_id}")

        db.commit()
        print(f"✅ Backfilled {len(clients)} clients")
    finally:
        db.close()


if __name__ == "__main__":
    main()
