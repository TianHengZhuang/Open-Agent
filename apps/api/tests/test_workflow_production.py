"""Phase 2 remainder + Phase 3: fan-in merge, webhook waits, pruning, error
workflows and pinned / partial preview execution."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select

from openagent.db.models import (
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.runtime import local_engine
from openagent.runtime.local_engine import MAX_PERSISTED_ITEMS_PER_NODE
from openagent.services.workflow_definition import validate_definition


def _definition(nodes, edges, *, settings=None):
    return {
        "schema_version": "1.1",
        "triggers": [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": edges,
        "variables": [],
        "settings": settings or {},
    }


def _branches(extra_merge_config, b_items):
    """Two parallel branches (each producing item lists) feeding one merge."""
    return _definition(
        [
            {"id": "a", "type": "set", "name": "A", "config": {"values": {"items": [{"id": 1}, {"id": 2}]}}},
            {
                "id": "am",
                "type": "map",
                "name": "A rows",
                "config": {"items": "{{ $json.items }}", "expression": "{{ $json }}"},
            },
            {"id": "b", "type": "set", "name": "B", "config": {"values": {"items": b_items}}},
            {
                "id": "bm",
                "type": "map",
                "name": "B rows",
                "config": {"items": "{{ $json.items }}", "expression": "{{ $json }}"},
            },
            {"id": "j", "type": "merge", "name": "Join", "config": extra_merge_config},
        ],
        [
            {"id": "e1", "from": "t", "to": "a"},
            {"id": "e2", "from": "t", "to": "b"},
            {"id": "e3", "from": "a", "to": "am"},
            {"id": "e4", "from": "b", "to": "bm"},
            {"id": "e5", "from": "am", "to": "j", "to_port": "a"},
            {"id": "e6", "from": "bm", "to": "j", "to_port": "b"},
        ],
    )


def _run(definition, trigger_input=None):
    return asyncio.run(local_engine.execute_definition(definition, trigger_input or {}))


# ---------------------------------------------------------------------------
# Merge fan-in
# ---------------------------------------------------------------------------


def test_merge_appends_every_branch_as_items():
    runs, _, status = _run(_branches({"mode": "append"}, [{"id": 3}]))
    assert status == "completed"

    join = next(run for run in runs if run["node_id"] == "j")
    assert [item["json"] for item in join["items"]] == [{"id": 1}, {"id": 2}, {"id": 3}]
    # pairedItem stays relative to the node's own (concatenated) input.
    assert [item["pairedItem"]["item"] for item in join["items"]] == [0, 1, 2]
    assert join["input_items"] == 3


def test_merge_by_key_joins_matching_items():
    runs, _, status = _run(
        _branches({"mode": "by_key", "field": "id"}, [{"id": 2, "city": "Dhaka"}])
    )
    assert status == "completed"

    join = next(run for run in runs if run["node_id"] == "j")
    assert [item["json"] for item in join["items"]] == [
        {"id": 1},
        {"id": 2, "city": "Dhaka"},
    ]


def test_merge_by_index_combines_positional_items():
    runs, _, status = _run(_branches({"mode": "by_index"}, [{"city": "Dhaka"}]))
    assert status == "completed"

    join = next(run for run in runs if run["node_id"] == "j")
    assert [item["json"] for item in join["items"]] == [
        {"id": 1, "city": "Dhaka"},
        {"id": 2},
    ]


# ---------------------------------------------------------------------------
# Pinned data + partial execution (preview only)
# ---------------------------------------------------------------------------


def _preview_definition():
    return _definition(
        [
            {"id": "seed", "type": "set", "name": "Seed", "config": {"values": {"v": 1}}},
            {
                "id": "bump",
                "type": "transform",
                "name": "Bump",
                "config": {"mapping": {"value": "{{ $json.v + 1 }}"}},
            },
        ],
        [
            {"id": "e1", "from": "t", "to": "seed"},
            {"id": "e2", "from": "seed", "to": "bump"},
        ],
        settings={
            "pinned_data": {
                "seed": {"pinned_at": "2026-11-23T00:00:00Z", "outputs": {"v": 10}},
            }
        },
    )


def test_preview_step_uses_pinned_upstream_items():
    result = asyncio.run(
        local_engine.run_single_node(_preview_definition(), "bump", use_pinned=True)
    )
    assert result["status"] == "succeeded"
    assert result["outputs"] == {"value": 11}
    assert "seed" in result["sources"]
    # `{{nodes.seed}}`-style reads also see the pin.
    assert result["inputs"]


def test_pinned_data_is_ignored_without_the_preview_flag():
    result = asyncio.run(local_engine.run_single_node(_preview_definition(), "bump"))
    assert "seed" not in result["sources"]


def test_partial_execution_accepts_supplied_upstream_output():
    result = asyncio.run(
        local_engine.run_single_node(
            _preview_definition(),
            "bump",
            upstream={"seed": {"items": [{"v": 7}]}},
        )
    )
    assert result["status"] == "succeeded"
    assert result["outputs"] == {"value": 8}
    assert result["sources"] == ["seed"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_settings_accept_error_workflow_and_reject_an_empty_one():
    definition = _definition(
        [{"id": "n1", "type": "set", "name": "N", "config": {"values": {"a": 1}}}],
        [{"id": "e1", "from": "t", "to": "n1"}],
        settings={"error_workflow_id": "00000000-0000-0000-0000-000000000002"},
    )
    verdict = validate_definition(definition)
    assert verdict.valid, verdict.errors

    definition["settings"]["error_workflow_id"] = "  "
    verdict = validate_definition(definition)
    assert not verdict.valid
    assert any(error.field == "settings.error_workflow_id" for error in verdict.errors)


# ---------------------------------------------------------------------------
# Persistence: webhook waits, pruning and error workflows
# ---------------------------------------------------------------------------


async def _save_workflow(db, org, name, slug, definition):
    workflow = Workflow(
        organization_id=org.id,
        name=name,
        slug=slug,
        status=WorkflowStatus.ACTIVE,
    )
    db.add(workflow)
    await db.flush()
    db.add(
        WorkflowVersion(
            workflow_id=workflow.id,
            version="1.0.0",
            schema_version="1.1",
            definition=definition,
        )
    )
    await db.commit()
    return workflow


@pytest.mark.asyncio
async def test_wait_for_webhook_parks_with_a_token_then_resumes(db_session, org_a):
    definition = _definition(
        [
            {"id": "n1", "type": "set", "name": "Seed", "config": {"values": {"x": 1}}},
            {
                "id": "n2",
                "type": "delay",
                "name": "Wait for callback",
                "config": {"wait_for_webhook": True},
            },
            {
                "id": "n3",
                "type": "set",
                "name": "After",
                "config": {"values": {"ticket": "{{ trigger.webhook.ticket }}"}},
            },
        ],
        [
            {"id": "e1", "from": "t", "to": "n1"},
            {"id": "e2", "from": "n1", "to": "n2"},
            {"id": "e3", "from": "n2", "to": "n3"},
        ],
    )
    workflow = await _save_workflow(db_session, org_a, "Webhook wait", "webhook-wait", definition)

    first = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    assert first["status"] == "waiting"

    execution = await db_session.get(WorkflowExecution, UUID(first["execution_id"]))
    assert execution.status == WorkflowExecutionStatus.WAITING
    wait = execution.metadata["wait"]
    assert wait["webhook_token"]
    assert wait["resume_at"] is None

    resumed = await local_engine.run_workflow(
        db_session,
        workflow=workflow,
        organization_id=org_a.id,
        triggered_by=None,
        definition=wait["definition"],
        resume_from=execution,
        trigger_input={"webhook": {"ticket": "T-1"}},
    )
    assert resumed["status"] == "completed"

    await db_session.refresh(execution)
    assert execution.status == WorkflowExecutionStatus.COMPLETED
    assert "wait" not in execution.metadata
    after = next(run for run in execution.metadata["node_runs"] if run["node_id"] == "n3")
    assert after["outputs"] == {"ticket": "T-1"}
    wait_run = next(run for run in execution.metadata["node_runs"] if run["node_id"] == "n2")
    assert wait_run["status"] == "succeeded"


@pytest.mark.asyncio
async def test_execution_data_is_pruned_for_large_item_lists(db_session, org_a):
    rows = [{"v": index} for index in range(MAX_PERSISTED_ITEMS_PER_NODE + 50)]
    definition = _definition(
        [
            {"id": "n1", "type": "set", "name": "Seed", "config": {"values": {"items": rows}}},
            {
                "id": "n2",
                "type": "map",
                "name": "Rows",
                "config": {"items": "{{ $json.items }}", "expression": "{{ $json }}"},
            },
        ],
        [{"id": "e1", "from": "t", "to": "n1"}, {"id": "e2", "from": "n1", "to": "n2"}],
    )
    workflow = await _save_workflow(db_session, org_a, "Big run", "big-run", definition)

    result = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "completed"
    # The caller keeps the full result; only the stored row is capped.
    rows_run = next(run for run in result["node_runs"] if run["node_id"] == "n2")
    assert len(rows_run["items"]) == MAX_PERSISTED_ITEMS_PER_NODE + 50

    execution = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    stored = next(run for run in execution.metadata["node_runs"] if run["node_id"] == "n2")
    assert len(stored["items"]) == MAX_PERSISTED_ITEMS_PER_NODE
    assert stored["items_truncated"] == 50
    assert len(execution.metadata["execution_data"]["nodes"]["n2"]) == MAX_PERSISTED_ITEMS_PER_NODE
    assert execution.metadata["summary"]["items_pruned"] >= 50


@pytest.mark.asyncio
async def test_failing_run_triggers_the_error_workflow(db_session, org_a):
    handler_definition = _definition(
        [
            {
                "id": "h1",
                "type": "set",
                "name": "Record",
                "config": {
                    "values": {
                        "failed_workflow": "{{ trigger.workflow.name }}",
                        "failed_node": "{{ trigger.lastNodeExecuted.node_name }}",
                    }
                },
            }
        ],
        [{"id": "e1", "from": "t", "to": "h1"}],
    )
    handler = await _save_workflow(
        db_session, org_a, "Error handler", "error-handler", handler_definition
    )

    failing_definition = _definition(
        [{"id": "f1", "type": "filter", "name": "Broken", "config": {"items": "not-a-list", "predicate": "true"}}],
        [{"id": "e1", "from": "t", "to": "f1"}],
        settings={"error_workflow_id": str(handler.id)},
    )
    failing = await _save_workflow(
        db_session, org_a, "Fails", "fails", failing_definition
    )

    result = await local_engine.run_workflow(
        db_session, workflow=failing, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "failed"

    execution = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    recorded = execution.metadata["error_workflow"]
    assert recorded["workflow_id"] == str(handler.id)
    assert recorded["status"] == "completed"

    handler_execution = (
        await db_session.execute(
            select(WorkflowExecution)
            .where(WorkflowExecution.workflow_id == handler.id)
            .order_by(WorkflowExecution.started_at.desc())
            .limit(1)
        )
    ).scalar_one()
    payload = handler_execution.metadata["trigger_input"]
    assert payload["workflow"]["name"] == "Fails"
    assert payload["lastNodeExecuted"]["node_name"] == "Broken"
    assert payload["error"]["code"] == "NODE_FAILED"
    assert payload["execution"]["id"] == str(execution.id)


@pytest.mark.asyncio
async def test_error_workflow_is_not_triggered_again_by_its_own_failure(db_session, org_a):
    handler_definition = _definition(
        [{"id": "h1", "type": "filter", "name": "Also broken", "config": {"items": "nope", "predicate": "true"}}],
        [{"id": "e1", "from": "t", "to": "h1"}],
    )
    handler = await _save_workflow(
        db_session, org_a, "Broken handler", "broken-handler", handler_definition
    )
    # The handler points at itself: the engine refuses to recurse.
    handler_definition["settings"] = {"error_workflow_id": str(handler.id)}
    version = (
        await db_session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == handler.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    version.definition = handler_definition
    await db_session.commit()

    result = await local_engine.run_workflow(
        db_session, workflow=handler, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "failed"

    execution = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    assert "error_workflow" not in (execution.metadata or {})

    count = (
        await db_session.execute(
            select(WorkflowExecution).where(WorkflowExecution.workflow_id == handler.id)
        )
    ).scalars().all()
    assert len(count) == 1
