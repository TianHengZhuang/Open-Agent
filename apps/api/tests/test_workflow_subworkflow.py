"""Backend tests for sub-workflow execution and cron schedule matching."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from openagent.db.models import (
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowExecutionTriggerType,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.runtime.local_engine import (
    MAX_SUBWORKFLOW_DEPTH,
    RunContext,
    execute_definition,
    run_workflow,
)
from openagent.runtime.schedule_runner import CronError, cron_matches, next_run_times
from openagent.runtime.workflow_ops import cancel_execution_tree

# SQLite's UUID columns use NUMERIC affinity, so an all-zero id collapses to
# an integer and cannot be read back. Real ids are random.
REMOTE_ID = uuid4()

SIMPLE = {
    "schema_version": "1.0",
    "triggers": [{"id": "trg_start", "type": "manual", "name": "Start", "config": {}}],
    "nodes": [
        {"id": "n_set", "type": "set", "name": "Set", "config": {"values": {"x": 1}}},
    ],
    "edges": [{"id": "e1", "from": "trg_start", "to": "n_set"}],
    "settings": {},
}

SUB = {
    "schema_version": "1.0",
    "triggers": [{"id": "trg_start", "type": "manual", "name": "Start", "config": {}}],
    "nodes": [
        {
            "id": "n_sub",
            "type": "subworkflow",
            "name": "Sub",
            "config": {"workflow_id": str(REMOTE_ID), "input": {"x": 2}},
        }
    ],
    "edges": [{"id": "e1", "from": "trg_start", "to": "n_sub"}],
    "settings": {},
}


async def _seed_remote(db_session, org_id):
    workflow = Workflow(
        id=REMOTE_ID,
        organization_id=org_id,
        name="Simple",
        slug="simple-sub",
        status=WorkflowStatus.ACTIVE,
        metadata={},
    )
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version="v1",
        definition=SIMPLE,
        status="published",
    )
    db_session.add(workflow)
    db_session.add(version)
    await db_session.flush()
    return workflow


@pytest.mark.asyncio
async def test_subworkflow_runs_referenced_workflow(db_session, org_a):
    await _seed_remote(db_session, org_a.id)

    runs, output, status = await execute_definition(
        SUB, {"input": {}}, parent=RunContext(db=db_session, organization_id=org_a.id)
    )

    step = next(r for r in runs if r["node_id"] == "n_sub")
    assert status == "completed", (step.get("error"), step.get("error_code"), step.get("outputs"), runs)
    assert step["status"] == "succeeded"
    assert step["outputs"]["workflow_id"] == str(REMOTE_ID)
    assert step["outputs"]["status"] == "completed"
    assert step["outputs"]["steps"] == 2  # trigger + set


@pytest.mark.asyncio
async def test_subworkflow_without_session_fails_cleanly(db_session, org_a):
    await _seed_remote(db_session, org_a.id)

    runs, _output, status = await execute_definition(SUB, {"input": {}})

    assert status == "failed"
    step = next(r for r in runs if r["node_id"] == "n_sub")
    assert step["status"] == "failed"
    assert step["error_code"] == "NO_CONTEXT"


@pytest.mark.asyncio
async def test_subworkflow_rejects_other_tenant(db_session, org_a, org_b):
    await _seed_remote(db_session, org_b.id)

    runs, _output, status = await execute_definition(
        SUB, {"input": {}}, parent=RunContext(db=db_session, organization_id=org_a.id)
    )

    assert status == "failed"
    step = next(r for r in runs if r["node_id"] == "n_sub")
    assert step["error_code"] == "SUBWORKFLOW_NOT_FOUND"


@pytest.mark.asyncio
async def test_subworkflow_cycle_is_rejected(db_session, org_a):
    await _seed_remote(db_session, org_a.id)

    runs, _output, status = await execute_definition(
        SUB,
        {"input": {}},
        parent=RunContext(
            db=db_session, organization_id=org_a.id, stack=(str(REMOTE_ID),)
        ),
    )
    assert status == "failed"
    step = next(r for r in runs if r["node_id"] == "n_sub")
    assert step["error_code"] == "SUBWORKFLOW_CYCLE"


async def _parent_workflow(db_session, org_id, definition, name="Parent flow"):
    parent = Workflow(
        organization_id=org_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        status=WorkflowStatus.ACTIVE,
        metadata={},
    )
    db_session.add(parent)
    await db_session.flush()
    db_session.add(
        WorkflowVersion(
            workflow_id=parent.id,
            version="v1",
            definition=definition,
            status="published",
        )
    )
    await db_session.flush()
    return parent


@pytest.mark.asyncio
async def test_subworkflow_creates_a_child_execution(db_session, org_a):
    child = await _seed_remote(db_session, org_a.id)
    parent = await _parent_workflow(db_session, org_a.id, SUB)

    result = await run_workflow(
        db_session,
        workflow=parent,
        organization_id=org_a.id,
        triggered_by=None,
        trigger_input={},
    )

    assert result["status"] == "completed", result.get("error_message")
    child_run = (
        await db_session.execute(
            select(WorkflowExecution).where(WorkflowExecution.workflow_id == child.id)
        )
    ).scalar_one()
    assert str(child_run.parent_execution_id) == result["execution_id"]
    assert str(child_run.root_execution_id) == result["execution_id"]
    assert child_run.status == WorkflowExecutionStatus.COMPLETED
    assert child_run.metadata["run_source"] == "subworkflow"
    assert child_run.metadata["node_runs"], "child node runs are stored on the child row"

    step = next(run for run in result["node_runs"] if run["node_id"] == "n_sub")
    assert step["outputs"]["execution_id"] == str(child_run.id)


@pytest.mark.asyncio
async def test_cancelling_a_parent_cancels_its_children(db_session, org_a):
    child = await _seed_remote(db_session, org_a.id)
    parent = await _parent_workflow(db_session, org_a.id, SIMPLE)
    parent_run = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=parent.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        metadata={},
    )
    db_session.add(parent_run)
    await db_session.flush()
    child_run = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=child.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.API,
        parent_execution_id=parent_run.id,
        root_execution_id=parent_run.id,
        metadata={},
    )
    db_session.add(child_run)
    await db_session.flush()

    cancelled = await cancel_execution_tree(db_session, parent_run, reason="test")
    await db_session.commit()

    assert set(cancelled) == {parent_run.id, child_run.id}
    await db_session.refresh(parent_run)
    await db_session.refresh(child_run)
    assert parent_run.status == WorkflowExecutionStatus.CANCELLED
    assert child_run.status == WorkflowExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_workflow_static_data_is_read_and_persisted(db_session, org_a):
    definition = {
        "schema_version": "1.1",
        "triggers": [{"id": "trg", "type": "manual", "name": "Start", "config": {}}],
        "nodes": [
            {
                "id": "n_read",
                "type": "set",
                "name": "Read",
                "config": {"values": {"last_id": "{{ $getWorkflowStaticData('global').last_id }}"}},
            }
        ],
        "edges": [{"id": "e1", "from": "trg", "to": "n_read"}],
        "settings": {},
    }
    workflow = await _parent_workflow(db_session, org_a.id, definition, name="Counter flow")
    workflow.static_data = {"global": {"last_id": 7}}
    await db_session.flush()

    result = await run_workflow(
        db_session,
        workflow=workflow,
        organization_id=org_a.id,
        triggered_by=None,
        trigger_input={},
    )

    step = next(run for run in result["node_runs"] if run["node_id"] == "n_read")
    assert step["outputs"]["last_id"] == 7
    await db_session.refresh(workflow)
    assert workflow.static_data == {"global": {"last_id": 7}}


@pytest.mark.asyncio
async def test_execute_workflow_trigger_exposes_the_parent_payload():
    definition = {
        "schema_version": "1.1",
        "triggers": [
            {
                "id": "trg",
                "type": "execute_workflow",
                "name": "Called by another workflow",
                "config": {"inputs": {"ticket_id": "string", "ignored": "string"}},
            }
        ],
        "nodes": [
            {"id": "n_seen", "type": "set", "name": "Seen", "config": {"values": {"seen": "{{ $json.ticket_id }}"}}},
        ],
        "edges": [{"id": "e1", "from": "trg", "to": "n_seen"}],
        "settings": {},
    }

    runs, _output, status = await execute_definition(definition, {"ticket_id": "T-1"})

    assert status == "completed"
    step = next(run for run in runs if run["node_id"] == "n_seen")
    assert step["outputs"]["seen"] == "T-1"
    trigger = next(run for run in runs if run["node_id"] == "trg")
    assert trigger["outputs"]["ignored"] is None


def test_subworkflow_depth_limit_matches_n8n():
    assert MAX_SUBWORKFLOW_DEPTH == 10


def test_cron_matches_basic_fields():
    when = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
    assert cron_matches("0 9 * * *", when)
    assert not cron_matches("1 9 * * *", when)
    assert cron_matches("0 9 * * sat", when)
    assert cron_matches("0 9 21 nov *", when)
    assert cron_matches("*/15 9 * * *", when)
    assert cron_matches("0 8-10 * * *", when)
    assert cron_matches("0 9 21 * *", when)
    assert not cron_matches("0 9 1,20 * *", when)


def test_cron_rejects_bad_expressions():
    when = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
    with pytest.raises(CronError):
        cron_matches("0 9 * *", when)
    with pytest.raises(CronError):
        cron_matches("99 9 * * *", when)
    with pytest.raises(CronError):
        cron_matches("0 9 * * funday", when)


def test_next_run_times_are_ordered():
    start = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
    upcoming = list(next_run_times("30 10 * * *", "UTC", count=2, from_time=start))
    assert len(upcoming) == 2
    assert upcoming[0] < upcoming[1]
    assert all(t.hour == 10 and t.minute == 30 for t in upcoming)
