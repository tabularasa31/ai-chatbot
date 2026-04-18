from __future__ import annotations

import importlib.util
from pathlib import Path


MAX_ALEMBIC_REVISION_LEN = 32
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "backend" / "migrations" / "versions"


def _load_revision(path: Path) -> str | None:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None, f"Could not load {path.name}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "revision", None)


def test_alembic_revisions_fit_version_num_limit() -> None:
    too_long: list[tuple[str, int]] = []

    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        revision = _load_revision(path)
        if revision is None:
            continue
        if len(revision) > MAX_ALEMBIC_REVISION_LEN:
            too_long.append((path.name, len(revision)))

    assert not too_long, (
        f"Alembic revision ids must be <= {MAX_ALEMBIC_REVISION_LEN} chars; "
        f"found too-long revisions: {too_long}"
    )


def test_alembic_revisions_are_unique() -> None:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []

    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        revision = _load_revision(path)
        assert revision is not None, f"{path.name} must define revision"
        prev = seen.get(revision)
        if prev is not None:
            duplicates.append((revision, path.name))
        else:
            seen[revision] = path.name

    assert not duplicates, f"Duplicate Alembic revision ids found: {duplicates}"


def test_chat_sticky_language_migration_metadata() -> None:
    path = MIGRATIONS_DIR / "chat_sticky_language_v1.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None, f"Could not load {path.name}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "chat_sticky_language_v1"
    assert module.down_revision == "gap_analyzer_indexes_v1"


def test_gap_jobs_retry_migration_metadata() -> None:
    path = MIGRATIONS_DIR / "gap_jobs_retry_v1.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None, f"Could not load {path.name}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "gap_jobs_retry_v1"
    assert module.down_revision == "chat_sticky_language_v1"
