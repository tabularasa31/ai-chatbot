#!/usr/bin/env python
"""Refuse a migration graph that would fail on deploy.

Two things about Alembic can only be discovered at ``alembic upgrade head``,
which on this project runs in Railway's release step — that is, in production,
during a deploy, with no way back except a human noticing. Both happened in the
same week:

* **Two heads.** Two branches each added a revision naming the same parent.
  Individually fine; merged, ``upgrade head`` refuses to guess which to run,
  the release step fails, and the service does not come up. Worse, the deploy
  carrying the fix is itself skipped while CI is red.
* **A revision id longer than the column.** ``alembic_version.version_num`` is
  ``VARCHAR(32)``. A 33-character id let the migration itself run and then
  failed on the write recording that it had — leaving the database migrated and
  Alembic convinced it was not.

Neither is visible in a diff. Both are one query away here.
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The width of ``alembic_version.version_num``, which Alembic creates and does
#: not widen. An id that does not fit is only discovered when the row is
#: written, i.e. after the migration's own DDL has already been applied.
VERSION_NUM_WIDTH = 32


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    problems: list[str] = []

    heads = scripts.get_heads()
    if len(heads) != 1:
        listed = "\n".join(f"    {h}" for h in sorted(heads))
        problems.append(
            f"{len(heads)} head revisions; `alembic upgrade head` needs exactly one.\n"
            f"{listed}\n\n"
            "  Two branches added a revision naming the same parent. Fix by\n"
            "  re-parenting the newer one onto the other:\n\n"
            "      down_revision = \"<the revision that landed first>\"\n\n"
            "  or, when both are already on main, by adding a merge revision.\n"
            "  Re-check immediately before merging, not when the branch opened —\n"
            "  main moves underneath it."
        )

    too_long = [
        rev.revision
        for rev in scripts.walk_revisions()
        if len(rev.revision) > VERSION_NUM_WIDTH
    ]
    if too_long:
        listed = "\n".join(f"    {r} ({len(r)} chars)" for r in sorted(too_long))
        problems.append(
            f"revision id longer than alembic_version.version_num "
            f"(VARCHAR({VERSION_NUM_WIDTH})):\n{listed}\n\n"
            "  The migration would run and then fail writing its own version\n"
            "  number, leaving the database ahead of what Alembic believes."
        )

    if problems:
        print("Migration graph will not deploy:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}\n", file=sys.stderr)
        return 1

    head = heads[0]
    print(f"[migrations] one head: {head} ({len(head)} chars, limit {VERSION_NUM_WIDTH})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
