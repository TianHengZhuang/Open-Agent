"""Add the definition schema version to workflow versions (additive only).

Existing rows are backfilled to '1.0' by the server default; new versions
record the definition's own schema_version, including the additive 1.1 shape.

Revision ID: 028_workflow_definition_schema
Revises: 027_add_developer_platform
"""

import sqlalchemy as sa

from alembic import op

revision = "028_workflow_definition_schema"
down_revision = "027_add_developer_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_versions",
        sa.Column(
            "schema_version",
            sa.String(length=20),
            nullable=False,
            server_default="1.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_versions", "schema_version")
