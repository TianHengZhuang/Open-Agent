"""Execution tree columns and per-workflow static data.

A sub-workflow call now creates a real child execution, so a run can be
navigated parent -> child and cancellation can cascade. ``workflows.static_data``
holds the values that survive between runs (n8n workflow static data).

Revision ID: 029_subworkflow_tree_static_data
Revises: 028_workflow_definition_schema
"""

import sqlalchemy as sa

from alembic import op

revision = "029_subworkflow_tree_static_data"
down_revision = "028_workflow_definition_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_executions",
        sa.Column("parent_execution_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "workflow_executions",
        sa.Column("root_execution_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflow_executions_parent_execution_id",
        "workflow_executions",
        "workflow_executions",
        ["parent_execution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_workflow_executions_root_execution_id",
        "workflow_executions",
        "workflow_executions",
        ["root_execution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workflow_executions_parent_execution_id",
        "workflow_executions",
        ["parent_execution_id"],
    )
    op.create_index(
        "ix_workflow_executions_root_execution_id",
        "workflow_executions",
        ["root_execution_id"],
    )
    op.create_index(
        "ix_workflow_executions_root",
        "workflow_executions",
        ["root_execution_id", "created_at"],
    )
    op.add_column(
        "workflows",
        sa.Column("static_data", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("workflows", "static_data")
    op.drop_index("ix_workflow_executions_root", table_name="workflow_executions")
    op.drop_index(
        "ix_workflow_executions_root_execution_id", table_name="workflow_executions"
    )
    op.drop_index(
        "ix_workflow_executions_parent_execution_id", table_name="workflow_executions"
    )
    op.drop_constraint(
        "fk_workflow_executions_root_execution_id",
        "workflow_executions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_workflow_executions_parent_execution_id",
        "workflow_executions",
        type_="foreignkey",
    )
    op.drop_column("workflow_executions", "root_execution_id")
    op.drop_column("workflow_executions", "parent_execution_id")
