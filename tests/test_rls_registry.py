"""Guard tests for the RLS registry in backend/core/rls.py.

A new tenant-scoped table must be classified (TENANT_SCOPED_TABLES /
CHILD_SCOPED_TABLES / AUTH_BOUNDARY_TABLES) and covered by a new Alembic
migration — these tests fail the SQLite suite the moment a table with a
``tenant_id`` column appears unclassified, so the second isolation contour
cannot silently rot.
"""

from __future__ import annotations

from backend.core import rls
from backend.models import Base


def _metadata_tables() -> dict:
    return Base.metadata.tables


def test_every_tenant_id_table_is_classified() -> None:
    tables_with_tenant_id = {
        name for name, table in _metadata_tables().items() if "tenant_id" in table.columns
    }
    classified = set(rls.TENANT_SCOPED_TABLES) | rls.AUTH_BOUNDARY_TABLES
    missing = tables_with_tenant_id - classified
    assert not missing, (
        f"Tables with tenant_id not covered by RLS registry: {sorted(missing)}. "
        "Add them to backend/core/rls.py and write a new Alembic migration "
        "(see the module docstring)."
    )


def test_registry_matches_schema() -> None:
    tables = _metadata_tables()
    for name in rls.TENANT_SCOPED_TABLES:
        assert name in tables, f"unknown table in TENANT_SCOPED_TABLES: {name}"
        assert "tenant_id" in tables[name].columns
    for name, (fk_col, parent) in rls.CHILD_SCOPED_TABLES.items():
        assert name in tables, f"unknown table in CHILD_SCOPED_TABLES: {name}"
        assert fk_col in tables[name].columns
        assert parent in rls.TENANT_SCOPED_TABLES, (
            f"child table {name} points at {parent}, which is not tenant-scoped"
        )
    for name in rls.AUTH_BOUNDARY_TABLES:
        assert name in tables, f"unknown table in AUTH_BOUNDARY_TABLES: {name}"
    overlap = set(rls.TENANT_SCOPED_TABLES) & rls.AUTH_BOUNDARY_TABLES
    assert not overlap


def test_nullable_tenant_classification_matches_schema() -> None:
    tables = _metadata_tables()
    for name in rls.TENANT_SCOPED_TABLES:
        is_nullable = tables[name].columns["tenant_id"].nullable
        assert is_nullable == (name in rls.NULLABLE_TENANT_TABLES), (
            f"{name}: tenant_id nullable={is_nullable} but NULLABLE_TENANT_TABLES "
            "disagrees — global (NULL-tenant) rows would be hidden or writes blocked"
        )


def test_statement_generation_shape() -> None:
    stmts = rls.rls_statements()
    covered = len(rls.TENANT_SCOPED_TABLES) + len(rls.CHILD_SCOPED_TABLES)
    # ENABLE + FORCE + DROP POLICY + CREATE POLICY per table.
    assert len(stmts) == covered * 4
    assert all("tenant_isolation" in s for s in stmts if "POLICY" in s)
