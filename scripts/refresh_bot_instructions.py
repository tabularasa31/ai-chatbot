#!/usr/bin/env python3
"""Refresh the preset block stored in ``bots.agent_instructions``.

``PRESET_SUPPORT_AGENT`` is copied into the row when a bot is created, so
edits to the preset never reach bots that already exist. This script swaps a
stored preset generation for the current one and leaves everything the owner
or the onboarding extractor wrote around it in place.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.chat.presets import PRESET_SUPPORT_AGENT
from backend.core.db import SessionLocal
from backend.models import Bot

logger = logging.getLogger(__name__)

_PRESET_GEN_2 = """\
You are a support assistant for {product_name}. Your job is to help users get answers from the provided documentation — clearly, honestly, and in the user's language.

Ground rules:
- Base every answer strictly on the retrieved context. If something isn't there, say so directly rather than guessing.
- When the context covers the question, be specific: name the exact setting, page, or section it describes.
- If a single missing detail would make your answer wrong or incomplete, ask one focused clarifying question instead of speculating.
- Stay on topic — politely decline anything unrelated to {product_name} and its docs.
- Match the user's language in every reply. Never switch languages mid-response.
- Keep it concise. Expand only when the user asks for more depth.

Formatting:
- Use Markdown when it adds clarity (lists, code blocks, headings).
- Only link to URLs that appear verbatim in the provided context.
- When you can't answer: "I don't have that information in the documentation. Feel free to reach out to the support team directly."

"""

_PRESET_GEN_1 = (
    _PRESET_GEN_2
    + "Follow the internal reasoning steps defined in the system configuration before every response."
)

# Longest first: generation 1 contains generation 2, and matching the shorter
# one inside it would leave generation 1's trailing line orphaned.
# Stripped, because the dashboard trims what it saves.
_LEGACY_BLOCKS = (_PRESET_GEN_1.strip(), _PRESET_GEN_2.strip())
_CURRENT_BLOCK = PRESET_SUPPORT_AGENT.strip()
_PRESET_MARKER = "You are a support assistant for {product_name}."

REFRESHED = "refreshed"
OVERWRITTEN = "overwritten"
CURRENT = "current"
CLEARED = "cleared"
CUSTOMIZED = "customized"


@dataclass
class RefreshStats:
    refreshed: int = 0
    overwritten: int = 0
    current: int = 0
    cleared: int = 0
    customized: int = 0

    @property
    def written(self) -> int:
        return self.refreshed + self.overwritten


def plan_refresh(stored: str | None, *, force: bool) -> tuple[str | None, str]:
    """Return the new value for one bot (None to leave it alone) and why."""
    if stored is None or not stored.strip():
        return None, CLEARED
    if _CURRENT_BLOCK in stored:
        return None, CURRENT
    for block in _LEGACY_BLOCKS:
        start = stored.find(block)
        if start >= 0:
            head, tail = stored[:start], stored[start + len(block) :]
            return head + _CURRENT_BLOCK + tail, REFRESHED
    if not force:
        return None, CUSTOMIZED
    marker = stored.find(_PRESET_MARKER)
    if marker >= 0:
        return stored[:marker] + _CURRENT_BLOCK, OVERWRITTEN
    return _CURRENT_BLOCK, OVERWRITTEN


def run_refresh(
    *,
    dry_run: bool,
    force: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> RefreshStats:
    db = session_factory()
    stats = RefreshStats()
    try:
        for bot in db.query(Bot).all():
            refreshed, outcome = plan_refresh(bot.agent_instructions, force=force)
            setattr(stats, outcome, getattr(stats, outcome) + 1)
            logger.info(
                "%s bot=%s tenant=%s stored=%d chars%s",
                outcome,
                bot.id,
                bot.tenant_id,
                len(bot.agent_instructions or ""),
                " [dry-run]" if dry_run else "",
            )
            if refreshed is not None and not dry_run:
                bot.agent_instructions = refreshed
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return stats
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite stored bot instructions from the current preset"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report the plan without writing rows"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also rewrite instructions whose preset block was edited — those edits are lost, "
        "though any text before the preset is kept",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = run_refresh(dry_run=args.dry_run, force=args.force)
    mode = "Would write" if args.dry_run else "Wrote"
    print(
        f"{mode} {stats.written} bot(s): {stats.refreshed} refreshed, "
        f"{stats.overwritten} overwritten. Left alone: {stats.current} already current, "
        f"{stats.cleared} cleared, {stats.customized} customized."
    )
    if stats.customized and not args.force:
        print("Customized bots keep their stored preset. Re-run with --force to replace it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
