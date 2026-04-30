"""Drop legacy manual-QA eval tables (testers, eval_sessions, eval_results).

The ``backend/eval/`` package was a REST harness for manual QA testers.
It is replaced by an automated eval pipeline (``backend/evals/``) that
stores runs in Langfuse Datasets and does not need DB tables. Drop the
unused tables.

Idempotent: each step checks live DB state before acting.

Revision ID: drop_legacy_eval_tables_v1
Revises: embeddings_entities_v1
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "drop_legacy_eval_tables_v1"
down_revision = "embeddings_entities_v1"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # Drop in FK-safe order: results → sessions → testers.
    if _has_table(insp, "eval_results"):
        op.drop_table("eval_results")
    if _has_table(insp, "eval_sessions"):
        op.drop_table("eval_sessions")
    if _has_table(insp, "testers"):
        op.drop_table("testers")


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported for this migration")
