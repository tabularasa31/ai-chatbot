"""Rename eval_sessions.tenant_id back to bot_id

After switching the eval gate to resolve by Bot.public_id (ADR 0001),
the column stores a Bot.public_id value; the name tenant_id is
misleading. Revert to bot_id to match the schema's eval_qa_mvp_v1
origin.

Idempotent: each step checks live DB state before acting.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "eval_sessions_tenant_to_bot"
down_revision = "67aaa83e5689"
branch_labels = None
depends_on = None


def _col(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def _idx(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if _col(insp, "eval_sessions", "tenant_id") and not _col(insp, "eval_sessions", "bot_id"):
        with op.batch_alter_table("eval_sessions") as batch_op:
            batch_op.alter_column("tenant_id", new_column_name="bot_id")

    insp = sa_inspect(bind)
    if _idx(insp, "eval_sessions", "ix_eval_sessions_tenant_id"):
        op.drop_index("ix_eval_sessions_tenant_id", table_name="eval_sessions")
    insp = sa_inspect(bind)
    if not _idx(insp, "eval_sessions", "ix_eval_sessions_bot_id"):
        op.create_index("ix_eval_sessions_bot_id", "eval_sessions", ["bot_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if _col(insp, "eval_sessions", "bot_id") and not _col(insp, "eval_sessions", "tenant_id"):
        with op.batch_alter_table("eval_sessions") as batch_op:
            batch_op.alter_column("bot_id", new_column_name="tenant_id")

    insp = sa_inspect(bind)
    if _idx(insp, "eval_sessions", "ix_eval_sessions_bot_id"):
        op.drop_index("ix_eval_sessions_bot_id", table_name="eval_sessions")
    insp = sa_inspect(bind)
    if not _idx(insp, "eval_sessions", "ix_eval_sessions_tenant_id"):
        op.create_index("ix_eval_sessions_tenant_id", "eval_sessions", ["tenant_id"])
