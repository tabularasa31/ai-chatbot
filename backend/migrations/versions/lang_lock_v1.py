"""Add language_locked flag to chats.

Once True, the chat's language is fixed (using last_response_language) and
all subsequent turns skip language detection and stick to that language.
Set by the lock heuristic (high-confidence first turn or two consistent
reliable turns); never reset on existing chats.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "lang_lock_v1"
down_revision = "add_clarification_count_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("chats")}
    if "language_locked" not in columns:
        op.add_column(
            "chats",
            sa.Column(
                "language_locked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported for this migration")
