"""fix_schema_drift

Revision ID: 67aaa83e5689
Revises: 47353c805cec
Create Date: 2026-04-19

Reconciles model metadata against the live schema after the
clients→tenants and client_id→tenant_id renames (93e5ff7b6924)
and eval_sessions bot_id→tenant_id rename.

Idempotent: every step checks actual DB state before acting,
so re-running after a partial apply is safe.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision = "67aaa83e5689"
down_revision = "47353c805cec"
branch_labels = None
depends_on = None


def _idx(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def _uq(insp, table: str, name: str) -> bool:
    return any(u["name"] == name for u in insp.get_unique_constraints(table))


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa_inspect(conn)

    # ── 1. tenants table: fix indexes after clients→tenants table rename ─────
    # ix_clients_api_key → ix_tenants_api_key  (unique index on api_key)
    if _idx(insp, "tenants", "ix_clients_api_key"):
        op.drop_index("ix_clients_api_key", table_name="tenants")
    if not _idx(insp, "tenants", "ix_tenants_api_key"):
        op.create_index("ix_tenants_api_key", "tenants", ["api_key"], unique=True)

    # uq_clients_public_id → uq_tenants_public_id  (unique constraint on public_id)
    if _uq(insp, "tenants", "uq_clients_public_id"):
        with op.batch_alter_table("tenants") as batch_op:
            batch_op.drop_constraint("uq_clients_public_id", type_="unique")
    if not _uq(insp, "tenants", "uq_tenants_public_id"):
        with op.batch_alter_table("tenants") as batch_op:
            batch_op.create_unique_constraint("uq_tenants_public_id", ["public_id"])

    # ix_tenants_public_id: separate non-unique index from Column(index=True)
    if not _idx(insp, "tenants", "ix_tenants_public_id"):
        op.create_index("ix_tenants_public_id", "tenants", ["public_id"])

    # ── 2. Per-table client_id → tenant_id index renames ─────────────────────
    _client_tenant_renames = [
        ("chats",              "ix_chats_client_id",              "ix_chats_tenant_id"),
        ("documents",          "ix_documents_client_id",          "ix_documents_tenant_id"),
        ("url_sources",        "ix_url_sources_client_id",        "ix_url_sources_tenant_id"),
        ("escalation_tickets", "ix_escalation_tickets_client_id", "ix_escalation_tickets_tenant_id"),
        ("pii_events",         "ix_pii_events_client_id",         "ix_pii_events_tenant_id"),
        ("users",              "ix_users_client_id",              "ix_users_tenant_id"),
    ]
    for table, old, new in _client_tenant_renames:
        if _idx(insp, table, old):
            op.drop_index(old, table_name=table)
        if not _idx(insp, table, new):
            op.create_index(new, table, ["tenant_id"])

    # ── 3. eval_sessions: bot_id → tenant_id index rename ────────────────────
    if _idx(insp, "eval_sessions", "ix_eval_sessions_bot_id"):
        op.drop_index("ix_eval_sessions_bot_id", table_name="eval_sessions")
    if not _idx(insp, "eval_sessions", "ix_eval_sessions_tenant_id"):
        op.create_index("ix_eval_sessions_tenant_id", "eval_sessions", ["tenant_id"])

    # ── 4. Drop extra index not present in model ──────────────────────────────
    # escalation_tickets.created_at was indexed in fi_esc_v1 but model has no index=True
    if _idx(insp, "escalation_tickets", "ix_escalation_tickets_created_at"):
        op.drop_index("ix_escalation_tickets_created_at", table_name="escalation_tickets")

    # ── 5. Fix Enum column widths (compare_type=True detects VARCHAR(N) drift) ─
    # In PostgreSQL these are safe narrowing changes (existing data uses short values).
    # batch_alter_table recreates the table on SQLite, issues ALTER COLUMN on PG.
    with op.batch_alter_table("escalation_tickets") as batch_op:
        batch_op.alter_column(
            "trigger",
            type_=sa.Enum(
                "low_similarity", "no_documents", "user_request", "answer_rejected",
                name="escalationtrigger", native_enum=False,
            ),
            existing_type=sa.String(32),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "priority",
            type_=sa.Enum(
                "low", "medium", "high", "critical",
                name="escalationpriority", native_enum=False,
            ),
            existing_type=sa.String(32),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status",
            type_=sa.Enum(
                "open", "in_progress", "resolved",
                name="escalationstatus", native_enum=False,
            ),
            existing_type=sa.String(32),
            existing_nullable=False,
        )

    with op.batch_alter_table("messages") as batch_op:
        batch_op.alter_column(
            "feedback",
            type_=sa.Enum("none", "up", "down", name="messagefeedback", native_enum=False),
            existing_type=sa.Enum(
                "none", "positive", "negative", name="messagefeedback", native_enum=False,
            ),
            existing_nullable=False,
        )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported for this migration")
