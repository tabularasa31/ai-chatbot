#!/usr/bin/env python3
"""Refresh the preset block stored in ``bots.agent_instructions``.

``PRESET_SUPPORT_AGENT`` is copied into the row when a bot is created, so
edits to the preset never reach bots that already exist. This script rewrites
the stored copy: anything the onboarding extractor prepended (the company
description) is preserved, the preset block itself is replaced.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.chat.presets import PRESET_SUPPORT_AGENT
from backend.core.db import SessionLocal
from backend.models import Bot

logger = logging.getLogger(__name__)

# The placeholder is stored raw and substituted at prompt-build time.
_PRESET_MARKER = "You are a support assistant for {product_name}."


def _refreshed_instructions(stored: str | None, *, force: bool) -> str | None:
    """New value for one bot, or None to leave the row untouched.

    A cleared field stays cleared — the owner turned the instructions off.
    """
    if stored is None or not stored.strip():
        return None
    start = stored.find(_PRESET_MARKER)
    if start < 0:
        return PRESET_SUPPORT_AGENT if force else None
    refreshed = stored[:start] + PRESET_SUPPORT_AGENT
    return refreshed if refreshed != stored else None


def run_refresh(
    *,
    dry_run: bool,
    force: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    db = session_factory()
    updated = 0
    try:
        for bot in db.query(Bot).all():
            refreshed = _refreshed_instructions(bot.agent_instructions, force=force)
            if refreshed is None:
                continue
            updated += 1
            logger.info(
                "refresh_bot_instructions_update",
                extra={
                    "bot_id": str(bot.id),
                    "tenant_id": str(bot.tenant_id),
                    "custom_prefix_kept": len(refreshed) > len(PRESET_SUPPORT_AGENT),
                    "dry_run": dry_run,
                },
            )
            if not dry_run:
                bot.agent_instructions = refreshed
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return updated
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite stored bot instructions from the current preset"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report updates without writing rows"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also overwrite instructions that no longer contain the preset (owner-written text is lost)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    updated = run_refresh(dry_run=args.dry_run, force=args.force)
    mode = "Would update" if args.dry_run else "Updated"
    print(f"{mode} {updated} bot instruction record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
