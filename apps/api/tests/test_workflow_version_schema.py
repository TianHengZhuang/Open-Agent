"""Migration and persistence tests for workflow version schema_version."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select

from openagent.api.v1.workflows import _create_version
from openagent.db.models import Workflow, WorkflowVersion


@pytest.mark.asyncio
async def test_create_version_persists_definition_schema_version(db_session, org_a, user_a):
    workflow = Workflow(
        organization_id=org_a.id,
        name="Schema version test",
        slug="schema-version-test",
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)

    v1 = await _create_version(
        db_session,
        workflow,
        {"schema_version": "1.1", "triggers": [], "nodes": [], "edges": [],
         "variables": [], "settings": {}},
        user_a.id,
    )
    v2 = await _create_version(
        db_session,
        workflow,
        {"schema_version": "1.0", "triggers": [], "nodes": [], "edges": [],
         "variables": [], "settings": {}},
        user_a.id,
    )
    await db_session.commit()

    stored = (await db_session.execute(
        select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
    )).scalars().all()
    by_name = {row.version: row for row in stored}
    assert by_name[v1.version].schema_version == "1.1"
    assert by_name[v2.version].schema_version == "1.0"
    # Backward compatibility: legacy rows default to 1.0 at the model level.
    legacy = WorkflowVersion(workflow_id=workflow.id, version="v99", definition={})
    db_session.add(legacy)
    await db_session.flush()
    assert legacy.schema_version == "1.0"


def test_alembic_revision_chain_includes_additive_migration():
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    # Resolve the script directory from the API root, not the pytest cwd.
    config.set_main_option("script_location", str(api_root / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["029_subworkflow_tree_static_data"]

    revision = script.get_revision("028_workflow_definition_schema")
    assert revision is not None
    assert revision.down_revision == "027_add_developer_platform"
    assert "schema_version" in Path(revision.path).read_text(encoding="utf-8")
