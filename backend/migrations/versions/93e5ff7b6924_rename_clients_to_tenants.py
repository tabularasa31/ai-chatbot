"""rename clients to tenants

Revision ID: 93e5ff7b6924
Revises: gap_jobs_retry_v1
Create Date: 2026-04-19

Renames:
  - table  clients                   → tenants
  - column users.client_id           → users.tenant_id
  - column documents.client_id       → documents.tenant_id
  - column url_sources.client_id     → url_sources.tenant_id
  - column quick_answers.client_id   → quick_answers.tenant_id
  - column chats.client_id           → chats.tenant_id
  - column escalation_tickets.client_id → escalation_tickets.tenant_id
  - column pii_events.client_id      → pii_events.tenant_id
  - column user_sessions.client_id   → user_sessions.tenant_id
  - column eval_sessions.bot_id      → eval_sessions.tenant_id
  - FK  fk_users_client_id           → fk_users_tenant_id
  - UQ  uq_escalation_client_ticket_number → uq_escalation_tenant_ticket_number

All steps are idempotent: each rename/drop is skipped if the source
object no longer exists, so re-running after a partial apply is safe.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "93e5ff7b6924"
down_revision = "gap_jobs_retry_v1"
branch_labels = None
depends_on = None

_SIMPLE_FK_TABLES = [
    "documents",
    "url_sources",
    "quick_answers",
    "chats",
    "escalation_tickets",
    "pii_events",
    "user_sessions",
]


def _col(inspector, table: str, name: str) -> bool:
    return any(c["name"] == name for c in inspector.get_columns(table))


def _fk(inspector, table: str, name: str) -> bool:
    return any(fk["name"] == name for fk in inspector.get_foreign_keys(table))


def _uq(inspector, table: str, name: str) -> bool:
    return any(u["name"] == name for u in inspector.get_unique_constraints(table))


def _idx(inspector, table: str, name: str) -> bool:
    return any(i["name"] == name for i in inspector.get_indexes(table))


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa_inspect(conn)
    tables = insp.get_table_names()

    if "clients" in tables and "tenants" not in tables:
        op.rename_table("clients", "tenants")

    with op.batch_alter_table("users") as batch_op:
        if _fk(insp, "users", "fk_users_client_id"):
            batch_op.drop_constraint("fk_users_client_id", type_="foreignkey")
        if _col(insp, "users", "client_id"):
            batch_op.alter_column("client_id", new_column_name="tenant_id")
        if not _fk(insp, "users", "fk_users_tenant_id"):
            batch_op.create_foreign_key(
                "fk_users_tenant_id", "tenants", ["tenant_id"], ["id"],
                ondelete="SET NULL", use_alter=True,
            )

    for table in _SIMPLE_FK_TABLES:
        if _col(insp, table, "client_id"):
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("client_id", new_column_name="tenant_id")

    with op.batch_alter_table("escalation_tickets") as batch_op:
        if _uq(insp, "escalation_tickets", "uq_escalation_client_ticket_number"):
            batch_op.drop_constraint("uq_escalation_client_ticket_number", type_="unique")
        if not _uq(insp, "escalation_tickets", "uq_escalation_tenant_ticket_number"):
            batch_op.create_unique_constraint(
                "uq_escalation_tenant_ticket_number", ["tenant_id", "ticket_number"]
            )

    if _col(insp, "eval_sessions", "bot_id"):
        with op.batch_alter_table("eval_sessions") as batch_op:
            batch_op.alter_column("bot_id", new_column_name="tenant_id")

    with op.batch_alter_table("user_sessions") as batch_op:
        if _idx(insp, "user_sessions", "ix_user_sessions_client_user"):
            batch_op.drop_index("ix_user_sessions_client_user")
        if _idx(insp, "user_sessions", "uq_user_sessions_client_user_active"):
            batch_op.drop_index("uq_user_sessions_client_user_active")
        if not _idx(insp, "user_sessions", "ix_user_sessions_tenant_user"):
            batch_op.create_index("ix_user_sessions_tenant_user", ["tenant_id", "user_id"])
        if not _idx(insp, "user_sessions", "uq_user_sessions_tenant_user_active"):
            batch_op.create_index(
                "uq_user_sessions_tenant_user_active",
                ["tenant_id", "user_id"],
                unique=True,
                postgresql_where="session_ended_at IS NULL",
                sqlite_where="session_ended_at IS NULL",
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa_inspect(conn)

    with op.batch_alter_table("user_sessions") as batch_op:
        if _idx(insp, "user_sessions", "uq_user_sessions_tenant_user_active"):
            batch_op.drop_index("uq_user_sessions_tenant_user_active")
        if _idx(insp, "user_sessions", "ix_user_sessions_tenant_user"):
            batch_op.drop_index("ix_user_sessions_tenant_user")
        if not _idx(insp, "user_sessions", "ix_user_sessions_client_user"):
            batch_op.create_index("ix_user_sessions_client_user", ["tenant_id", "user_id"])
        if not _idx(insp, "user_sessions", "uq_user_sessions_client_user_active"):
            batch_op.create_index(
                "uq_user_sessions_client_user_active",
                ["tenant_id", "user_id"],
                unique=True,
                postgresql_where="session_ended_at IS NULL",
                sqlite_where="session_ended_at IS NULL",
            )

    if _col(insp, "eval_sessions", "tenant_id"):
        with op.batch_alter_table("eval_sessions") as batch_op:
            batch_op.alter_column("tenant_id", new_column_name="bot_id")

    with op.batch_alter_table("escalation_tickets") as batch_op:
        if _uq(insp, "escalation_tickets", "uq_escalation_tenant_ticket_number"):
            batch_op.drop_constraint("uq_escalation_tenant_ticket_number", type_="unique")
        if not _uq(insp, "escalation_tickets", "uq_escalation_client_ticket_number"):
            batch_op.create_unique_constraint(
                "uq_escalation_client_ticket_number", ["tenant_id", "ticket_number"]
            )

    for table in reversed(_SIMPLE_FK_TABLES):
        if _col(insp, table, "tenant_id"):
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("tenant_id", new_column_name="client_id")

    with op.batch_alter_table("users") as batch_op:
        if _fk(insp, "users", "fk_users_tenant_id"):
            batch_op.drop_constraint("fk_users_tenant_id", type_="foreignkey")
        if _col(insp, "users", "tenant_id"):
            batch_op.alter_column("tenant_id", new_column_name="client_id")
        if not _fk(insp, "users", "fk_users_client_id"):
            batch_op.create_foreign_key(
                "fk_users_client_id", "clients", ["client_id"], ["id"],
                ondelete="SET NULL", use_alter=True,
            )

    tables = insp.get_table_names()
    if "tenants" in tables and "clients" not in tables:
        op.rename_table("tenants", "clients")
