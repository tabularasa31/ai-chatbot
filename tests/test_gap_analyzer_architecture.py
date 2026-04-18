"""Architecture guardrails for backend/gap_analyzer/.

These tests enforce the structural invariants established by the 5-phase
refactor.  They are intentionally lightweight (no DB, no HTTP) and run as
part of the normal SQLite suite.

Invariants checked:
1. File-size limits — no single module may regrow past its threshold.
2. Import-graph boundaries — pipelines must not import orchestrator;
   _repo submodules must not import pipelines or orchestrator.
3. Public re-exports — key symbols remain importable from their
   backward-compat locations in repository.py and orchestrator.py.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

GAP_DIR = Path(__file__).parent.parent / "backend" / "gap_analyzer"

# ---------------------------------------------------------------------------
# 1. File-size limits (lines)
# ---------------------------------------------------------------------------

FILE_SIZE_LIMITS: dict[str, int] = {
    # thin proxy shells
    "orchestrator.py": 900,
    "repository.py": 500,
    # pipeline helpers
    "pipelines/mode_a.py": 300,
    "pipelines/mode_b.py": 400,
    "pipelines/link_sync.py": 250,
    "pipelines/drafts.py": 80,
    # read-side builders
    "read_models.py": 350,
    # pure-math / pure-logic helpers
    "_math.py": 120,
    "_classification.py": 130,
    # _repo submodules
    "_repo/bm25_cache.py": 250,
    "_repo/capabilities.py": 100,
    "_repo/job_queue.py": 400,
    "_repo/mode_a_queries.py": 250,
    "_repo/mode_b_queries.py": 400,
    "_repo/records.py": 130,
    "_repo/signals.py": 150,
    "_repo/summary.py": 150,
}


@pytest.mark.parametrize("rel_path,limit", FILE_SIZE_LIMITS.items())
def test_file_size_within_limit(rel_path: str, limit: int) -> None:
    path = GAP_DIR / rel_path
    assert path.exists(), f"{path} does not exist"
    with path.open() as f:
        line_count = sum(1 for _ in f)
    assert line_count <= limit, (
        f"{rel_path} has {line_count} lines — exceeds the {limit}-line guardrail. "
        "Decompose or raise the limit deliberately."
    )


# ---------------------------------------------------------------------------
# 2. Import-graph boundaries (static AST analysis — no imports executed)
# ---------------------------------------------------------------------------

def _collect_imports(path: Path) -> set[str]:
    """Return all dotted module names imported (statically) in *path*."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


def _gap_imports(path: Path) -> set[str]:
    """Filter to only gap_analyzer-internal imports."""
    prefix = "backend.gap_analyzer"
    return {n for n in _collect_imports(path) if n == prefix or n.startswith(f"{prefix}.")}


PIPELINE_FILES = [p for p in (GAP_DIR / "pipelines").glob("*.py") if p.name != "__init__.py"]
REPO_SUBMODULE_FILES = [
    p for p in (GAP_DIR / "_repo").glob("*.py")
    if p.name != "__init__.py"
]
ORCHESTRATOR_MODULE = "backend.gap_analyzer.orchestrator"


@pytest.mark.parametrize("path", PIPELINE_FILES, ids=lambda p: p.name)
def test_pipelines_do_not_import_orchestrator(path: Path) -> None:
    imports = _gap_imports(path)
    assert ORCHESTRATOR_MODULE not in imports, (
        f"pipelines/{path.name} imports orchestrator — pipelines must remain "
        "orchestrator-agnostic to avoid circular dependencies."
    )


@pytest.mark.parametrize("path", REPO_SUBMODULE_FILES, ids=lambda p: p.name)
def test_repo_submodules_do_not_import_pipelines(path: Path) -> None:
    imports = _gap_imports(path)
    pipeline_imports = {m for m in imports if "pipelines" in m.split(".")}
    assert not pipeline_imports, (
        f"_repo/{path.name} imports pipeline modules {pipeline_imports}. "
        "_repo submodules must not depend on the pipeline layer."
    )


@pytest.mark.parametrize("path", REPO_SUBMODULE_FILES, ids=lambda p: p.name)
def test_repo_submodules_do_not_import_orchestrator(path: Path) -> None:
    imports = _gap_imports(path)
    assert ORCHESTRATOR_MODULE not in imports, (
        f"_repo/{path.name} imports orchestrator — _repo must not depend on the orchestration layer."
    )


# ---------------------------------------------------------------------------
# 3. Public re-exports still importable from backward-compat locations
# ---------------------------------------------------------------------------

REPOSITORY_PUBLIC_SYMBOLS = [
    "SqlAlchemyGapAnalyzerRepository",
    "GapAnalyzerRepository",
    "invalidate_bm25_cache_for_tenant",
    "StoredGapSignalState",
    "ModeACorpusChunk",
    "ModeADismissalRecord",
    "ModeBQuestionRecord",
    "ModeBClusterRecord",
    "TenantVectorMatch",
    "TenantBm25Match",
    "GapJobEnqueueResult",
    "GapJobRecord",
    "_GAP_JOB_LAST_ERROR_MAX_CHARS",
]

ORCHESTRATOR_PUBLIC_SYMBOLS = [
    "GapAnalyzerOrchestrator",
    "GapResourceNotFoundError",
    # internal symbols re-exported for test backward-compat
    "_tokenize",
    "_MutableModeBCluster",
    "_ModeBClusterUpdateRejectedError",
    "_update_mode_b_cluster",
]


@pytest.mark.parametrize("symbol", REPOSITORY_PUBLIC_SYMBOLS)
def test_repository_re_exports(symbol: str) -> None:
    mod = importlib.import_module("backend.gap_analyzer.repository")
    assert hasattr(mod, symbol), (
        f"backend.gap_analyzer.repository no longer exports '{symbol}'. "
        "Add a re-export to preserve the public API."
    )


@pytest.mark.parametrize("symbol", ORCHESTRATOR_PUBLIC_SYMBOLS)
def test_orchestrator_re_exports(symbol: str) -> None:
    mod = importlib.import_module("backend.gap_analyzer.orchestrator")
    assert hasattr(mod, symbol), (
        f"backend.gap_analyzer.orchestrator no longer exports '{symbol}'. "
        "Add a re-export to preserve the public API."
    )
