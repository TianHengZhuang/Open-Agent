"""Backend tests for sub-workflow execution and cron schedule matching."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from openagent.db.models import Workflow, WorkflowStatus, WorkflowVersion
from openagent.runtime.local_engine import RunContext, execute_definition
from openagent.runtime.schedule_runner import CronError, cron_matches, next_run_times

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
