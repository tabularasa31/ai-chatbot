"""Rename tenant_profiles.modules to topics.

The DB column was named 'modules' while the API, extractor, and public
terminology already used 'topics'. This migration aligns the column name
with the rest of the stack and removes the drift.

Revision ID: rename_tenant_profile_modules_to_topics_v1
Revises: widen_document_file_type_v1
Create Date: 2026-04-26
"""

from alembic import op

revision = "rename_tp_modules_to_topics_v1"
down_revision = "ix_state_machine_fields_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tenant_profiles", "modules", new_column_name="topics")


def downgrade() -> None:
    op.alter_column("tenant_profiles", "topics", new_column_name="modules")
