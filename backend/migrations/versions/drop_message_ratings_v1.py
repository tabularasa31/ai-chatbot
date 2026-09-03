"""Drop messages.feedback and messages.ideal_answer.

The dashboard rating UI, the feedback routes and every reader of these
columns are gone; nothing writes them any more. Gap Analyzer keeps its
weight formula on the signals the chat pipeline still emits (low
confidence, fallback, rejection, escalation).

Revision ID: drop_message_ratings_v1
Revises: add_documents_script_v1
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "drop_message_ratings_v1"
down_revision = "add_documents_script_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("feedback")
        batch_op.drop_column("ideal_answer")


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "feedback",
                sa.String(length=4),
                nullable=False,
                server_default="none",
            )
        )
        batch_op.add_column(sa.Column("ideal_answer", sa.Text(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE messages ADD CONSTRAINT messages_feedback_check "
            "CHECK (feedback IN ('none', 'up', 'down'))"
        )
