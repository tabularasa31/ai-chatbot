#!/usr/bin/env python3
"""Backfill: split legacy ``bots.agent_instructions`` into ``preset`` + ``custom_instructions``.

PR-1 introduced the new read model (``preset`` in code, tenant text in
``custom_instructions``); this script migrates rows that still carry the old
single-blob ``agent_instructions``, so ``effective_agent_instructions`` keeps
returning the same rendered text through the new columns.

Idempotent and dry-run by default: run again after ``--apply`` and every row
reports as ``skip``.
"""

from __future__ import annotations

import argparse
import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterable

from sqlalchemy.orm import Session

from backend.chat.presets import PRESET_SUPPORT_AGENT
from backend.core.db import SessionLocal
from backend.models import Bot
from scripts.refresh_bot_instructions import _PRESET_GEN_1, _PRESET_GEN_2

logger = logging.getLogger(__name__)

SKIP = "skip"
EXCLUDED = "excluded"
PRESET_ONLY = "preset_only"
SPLIT = "split"
UNRECOGNIZED = "unrecognized"

SUPPORT_AGENT_PRESET = "support_agent"

# Longest first, same reasoning as the refresh script: generation 1 contains
# generation 2 as a substring, so it must be tried before generation 2.
_KNOWN_GENERATIONS = (_PRESET_GEN_1.strip(), _PRESET_GEN_2.strip(), PRESET_SUPPORT_AGENT.strip())

# The prod persona bot (see project memory: its agent_instructions is
# hand-authored and must not be rewritten). Extendable via --exclude-id.
DEFAULT_EXCLUDED_PREFIXES = ("8e9956e9",)


class BackfillAbort(RuntimeError):
    """Raised to stop the whole run before any writes happen."""


def classify(legacy: str, *, excluded: bool) -> tuple[str, str | None, str | None]:
    """Classify one bot's stripped legacy text.

    Returns ``(category, custom_instructions, preset)``. Caller always sets
    ``agent_instructions = None`` for every category except ``skip``.
    """
    if excluded:
        return EXCLUDED, legacy, None
    if legacy in _KNOWN_GENERATIONS:
        return PRESET_ONLY, None, SUPPORT_AGENT_PRESET
    for block in _KNOWN_GENERATIONS:
        count = legacy.count(block)
        if count == 0:
            continue
        if count > 1:
            # A known block repeated in the legacy text means the split point
            # is ambiguous; leave it for manual review instead of dropping a
            # copy of the preset into custom_instructions.
            return UNRECOGNIZED, legacy, None
        start = legacy.find(block)
        head, tail = legacy[:start], legacy[start + len(block) :]
        custom = "\n\n".join(part for part in (head.strip(), tail.strip()) if part) or None
        return SPLIT, custom, SUPPORT_AGENT_PRESET
    return UNRECOGNIZED, legacy, None


def _resolve_excluded_ids(
    db: Session, *, default_prefixes: Iterable[str], explicit_prefixes: Iterable[str]
) -> set[uuid.UUID]:
    all_ids = [row[0] for row in db.query(Bot.id).all()]
    resolved: set[uuid.UUID] = set()

    def _matches(prefix: str) -> list[uuid.UUID]:
        normalized = prefix.strip().lower()
        if not normalized:
            return []
        return [bid for bid in all_ids if str(bid).lower().startswith(normalized)]

    for prefix in default_prefixes:
        matches = _matches(prefix)
        if len(matches) > 1:
            raise BackfillAbort(
                f"--exclude-id {prefix!r} matches {len(matches)} bots; use a longer prefix"
            )
        resolved.update(matches)

    for prefix in explicit_prefixes:
        matches = _matches(prefix)
        if not matches and prefix.strip():
            raise BackfillAbort(f"--exclude-id {prefix!r} matches no bot")
        if len(matches) > 1:
            raise BackfillAbort(
                f"--exclude-id {prefix!r} matches {len(matches)} bots; use a longer prefix"
            )
        resolved.update(matches)

    return resolved


def _parse_bot_ids(raw_ids: Iterable[str]) -> list[uuid.UUID]:
    parsed = []
    for raw in raw_ids:
        try:
            parsed.append(uuid.UUID(raw))
        except ValueError as exc:
            raise BackfillAbort(f"--bot-id {raw!r} is not a valid UUID") from exc
    return parsed


def run_backfill(
    *,
    apply: bool,
    exclude_ids: Iterable[str] = (),
    bot_ids: Iterable[str] | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> tuple[Counter, list[str]]:
    """Run the backfill (or its dry-run plan) and return (stats, report_lines)."""
    db = session_factory()
    try:
        excluded = _resolve_excluded_ids(
            db, default_prefixes=DEFAULT_EXCLUDED_PREFIXES, explicit_prefixes=exclude_ids
        )

        query = db.query(Bot.id).order_by(Bot.id)
        if bot_ids is not None:
            wanted = _parse_bot_ids(bot_ids)
            query = query.filter(Bot.id.in_(wanted))
        all_ids = [row[0] for row in query.all()]

        stats: Counter = Counter()
        lines: list[str] = []
        for bot_id in all_ids:
            bot = db.query(Bot).filter(Bot.id == bot_id).with_for_update().one_or_none()
            if bot is None:
                db.rollback()
                continue  # deleted concurrently

            legacy = (bot.agent_instructions or "").strip()
            if not legacy:
                # Already on the new model, or a concurrent PATCH cleared it
                # after we locked the row (re-check).
                stats[SKIP] += 1
                skip_line = f"{bot.id} {bot.tenant_id} {SKIP} legacy_len=0 custom_len=0"
                lines.append(skip_line)
                logger.info(skip_line)
                db.rollback()
                continue

            category, custom, preset = classify(legacy, excluded=bot.id in excluded)
            stats[category] += 1
            line = (
                f"{bot.id} {bot.tenant_id} {category} "
                f"legacy_len={len(legacy)} custom_len={len(custom or '')}"
            )
            lines.append(line)
            logger.info(line)
            if apply:
                bot.custom_instructions = custom
                bot.preset = preset
                bot.agent_instructions = None
                db.commit()
            else:
                db.rollback()

        return stats, lines
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Split legacy bots.agent_instructions into preset + custom_instructions"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default is dry-run/report only)"
    )
    parser.add_argument(
        "--exclude-id",
        action="append",
        default=[],
        help="Full bot id or unique prefix to exclude (kept verbatim as custom_instructions); "
        "may be given multiple times",
    )
    parser.add_argument(
        "--bot-id",
        action="append",
        default=None,
        help="Restrict processing to this bot id (full UUID); may be given multiple times",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        stats, _lines = run_backfill(
            apply=args.apply, exclude_ids=args.exclude_id, bot_ids=args.bot_id
        )
    except BackfillAbort as exc:
        print(f"Aborted: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - surfaced as a plain abort message
        print(f"Aborted: DB error: {exc}")
        return 1

    # Per-bot lines are already logged as they're produced (run_backfill), so a
    # DB error mid-loop doesn't lose them; only the summary is printed here.
    mode = "Would write" if not args.apply else "Wrote"
    total = sum(v for k, v in stats.items() if k != SKIP)
    print(f"{mode} {total} bot(s): " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
