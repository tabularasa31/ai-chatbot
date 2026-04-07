from __future__ import annotations

import ast
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import backend.gap_analyzer as gap_analyzer
from backend.gap_analyzer.domain import CoveragePolicy, DraftGenerationPolicy
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.schemas import GapRunMode, RecalculateCommandResult
from backend.models import Base


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "backend" / "gap_analyzer"
MIGRATION_PATH = ROOT / "backend" / "migrations" / "versions" / "gap_analyzer_phase1_v1.py"
README_PATH = MODULE_DIR / "README.md"


def _imports_for(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("gap_analyzer_phase1_v1", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gap_analyzer_exports_are_restricted() -> None:
    assert gap_analyzer.__all__ == ["GapAnalyzerOrchestrator", "GapSignal"]
    assert gap_analyzer.GapAnalyzerOrchestrator is GapAnalyzerOrchestrator
    assert gap_analyzer.GapSignal is GapSignal


def test_gap_analyzer_domain_and_events_have_no_backend_imports() -> None:
    for filename in ("domain.py", "events.py"):
        imports = _imports_for(MODULE_DIR / filename)
        forbidden = [item for item in imports if item == "backend" or item.startswith("backend.")]
        assert not forbidden, f"{filename} should not import repo modules: {forbidden}"


def test_gap_analyzer_orchestrator_and_repository_avoid_cross_domain_imports() -> None:
    forbidden_prefixes = (
        "backend.chat",
        "backend.search",
        "backend.documents",
    )
    for filename in ("orchestrator.py", "repository.py"):
        imports = _imports_for(MODULE_DIR / filename)
        forbidden = [
            item for item in imports if any(item.startswith(prefix) for prefix in forbidden_prefixes)
        ]
        assert not forbidden, f"{filename} should not import cross-domain modules: {forbidden}"


def test_gap_analyzer_orchestrator_is_command_only_skeleton() -> None:
    orchestrator = GapAnalyzerOrchestrator()

    assert not hasattr(orchestrator, "list_active_gaps")
    assert not hasattr(orchestrator, "get_summary")

    signal = GapSignal(
        tenant_id=uuid4(),
        question_text="How does this work?",
        answer_confidence=0.4,
        was_rejected=False,
        was_escalated=False,
        user_thumbed_down=False,
    )

    with pytest.raises(NotImplementedError):
        asyncio.run(orchestrator.ingest_signal(signal))
    with pytest.raises(NotImplementedError):
        asyncio.run(orchestrator.run_mode_a(uuid4()))
    with pytest.raises(NotImplementedError):
        asyncio.run(orchestrator.run_mode_b(uuid4()))
    with pytest.raises(NotImplementedError):
        asyncio.run(orchestrator.request_recalculation(uuid4(), GapRunMode.both))


def test_gap_analyzer_phase1_policies_remain_data_only() -> None:
    policy = CoveragePolicy()
    assert policy.mode_a_gate == 0.45
    assert not hasattr(policy, "compute_score")
    assert not hasattr(policy, "classify")

    draft_policy = DraftGenerationPolicy()
    assert draft_policy.linked_primary_label_source == "mode_b"
    assert draft_policy.append_mode_a_example_questions is True


def test_gap_signal_default_timestamp_is_timezone_aware() -> None:
    signal = GapSignal(
        tenant_id=uuid4(),
        question_text="How does this work?",
        answer_confidence=0.4,
        was_rejected=False,
        was_escalated=False,
        user_thumbed_down=False,
    )
    assert signal.created_at.tzinfo is not None
    assert signal.created_at.utcoffset() is not None


def test_gap_analyzer_models_are_registered_in_metadata() -> None:
    table_names = set(Base.metadata.tables)
    assert {
        "gap_questions",
        "gap_clusters",
        "gap_doc_topics",
        "gap_dismissals",
        "gap_question_message_links",
    }.issubset(table_names)

    gap_questions = Base.metadata.tables["gap_questions"]
    assert "gap_signal_weight" in gap_questions.c
    assert "embedding" in gap_questions.c

    gap_clusters = Base.metadata.tables["gap_clusters"]
    assert "aggregate_signal_weight" in gap_clusters.c
    assert "linked_doc_topic_id" in gap_clusters.c

    gap_doc_topics = Base.metadata.tables["gap_doc_topics"]
    assert "extraction_chunk_hash" in gap_doc_topics.c
    assert "linked_cluster_id" in gap_doc_topics.c


def test_gap_analyzer_model_indexes_are_present() -> None:
    expected_indexes = {
        "ix_gap_clusters_tenant_status",
        "ix_gap_clusters_tenant_signal_weight",
        "ix_gap_doc_topics_tenant_status",
        "ix_gap_questions_tenant_cluster",
        "ix_gap_questions_tenant_signal_weight",
        "ix_gap_dismissals_tenant_gap",
        "ix_gap_question_links_gap_question",
        "ix_gap_question_links_user_message",
        "ix_gap_question_links_assistant_message",
        "ix_gap_question_links_session_id",
    }

    seen_indexes = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.name is not None
    }
    assert expected_indexes.issubset(seen_indexes)


def test_gap_analyzer_phase1_migration_contains_view_and_indexes() -> None:
    content = MIGRATION_PATH.read_text()

    assert "CREATE VIEW gap_unified AS" in content
    assert "gap_question_message_links" in content
    assert "ix_gap_clusters_tenant_status" in content
    assert "ix_gap_doc_topics_tenant_status" in content
    assert "ix_gap_questions_tenant_signal_weight" in content
    assert "ix_gap_dismissals_topic_embedding_ivfflat" in content
    assert "CAST(NULL AS FLOAT) AS aggregate_signal_weight" in content
    assert "CAST(NULL AS INTEGER) AS question_count" in content
    assert "ix_gap_question_links_user_message" in content
    assert "ix_gap_question_links_session_id" in content


def test_gap_analyzer_phase0_contracts_are_explicit_and_reviewable() -> None:
    content = README_PATH.read_text()

    assert "POST /gap-analyzer/recalculate" in content
    assert "202 Accepted" in content
    assert "Mode B label as the primary" in content
    assert "backend.search" in content
    assert "backend.chat" in content

    contract = RecalculateCommandResult(
        tenant_id=uuid4(),
        mode=GapRunMode.both,
        status="accepted",
    )
    assert contract.command_kind == "orchestration"
    assert contract.http_status_code == 202


def test_gap_analyzer_phase1_migration_applies_on_minimal_sqlite_schema() -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE clients (id CHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE users (id CHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE chats (id CHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE messages (id CHAR(36) PRIMARY KEY)"))

        context = MigrationContext.configure(connection)
        operations = Operations(context)

        with patch.object(migration, "op", operations):
            migration.upgrade()

        inspector = sa.inspect(connection)
        assert "gap_questions" in inspector.get_table_names()
        assert "gap_clusters" in inspector.get_table_names()
        assert "gap_doc_topics" in inspector.get_table_names()
        assert "gap_dismissals" in inspector.get_table_names()
        assert "gap_question_message_links" in inspector.get_table_names()
        assert "gap_unified" in inspector.get_view_names()
