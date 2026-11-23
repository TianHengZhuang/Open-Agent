"""Phase 2 execution contract: item envelopes, run modes, waits and resume."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from openagent.db.models import (
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.runtime import local_engine
from openagent.runtime.local_engine import RunContext
from openagent.runtime.schedule_runner import resume_due_waits
from openagent.services.workflow_definition import validate_definition


def _definition(nodes, edges, trigger_input_marker=True):
    return {
        "schema_version": "1.1",
        "triggers": [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": [{"id": f"e{i}", "from": src, "to": dst} for i, (src, dst) in enumerate(edges)],
        "variables": [],
        "settings": {},
    }


def _run(definition, trigger_input=None):
    return asyncio.run(local_engine.execute_definition(definition, trigger_input or {}))


def test_run_mode_each_runs_once_per_item():
    definition = _definition(
        [
            {"id": "seed", "type": "set", "name": "Seed", "config": {"values": {"items": [{"v": 1}, {"v": 2}]}}},
            {
                "id": "rows",
                "type": "map",
                "name": "Rows",
                "config": {"items": "{{ $json.items }}", "expression": "{{ $json }}"},
            },
            {
                "id": "double",
                "type": "transform",
                "name": "Double",
                "run_mode": "each",
                "config": {"mapping": {"result": "{{ $json.v * 2 }}"}},
            },
        ],
        [("t", "seed"), ("seed", "rows"), ("rows", "double")],
    )
    runs, output, status = _run(definition)

    assert status == "completed"
    double = next(run for run in runs if run["node_id"] == "double")
    assert [item["json"] for item in double["items"]] == [{"result": 2}, {"result": 4}]
    assert [item["pairedItem"]["item"] for item in double["items"]] == [0, 1]

    # A single-output node carries one item; a list node carries one per item.
    seed = next(run for run in runs if run["node_id"] == "seed")
    assert seed["items"] == [{"json": {"items": [{"v": 1}, {"v": 2}]}}]
    rows = next(run for run in runs if run["node_id"] == "rows")
    assert len(rows["items"]) == 2
    trigger = next(run for run in runs if run["node_id"] == "t")
    assert trigger["items"]


def test_run_mode_defaults_to_one_pass():
    definition = _definition(
        [
            {"id": "seed", "type": "set", "name": "Seed", "config": {"values": {"items": [1, 2, 3]}}},
            {"id": "join", "type": "set", "name": "Join", "config": {"values": {"done": True}}},
        ],
        [("t", "seed"), ("seed", "join")],
    )
    runs, _, _ = _run(definition)
    join = next(run for run in runs if run["node_id"] == "join")
    assert join["outputs"] == {"done": True}
    assert join["items"] == [{"json": {"done": True}}]


def test_paired_items_survive_sort_and_limit():
    definition = _definition(
        [
            {
                "id": "seed",
                "type": "set",
                "name": "Seed",
                "config": {"values": {"items": [{"v": 3}, {"v": 1}, {"v": 2}]}},
            },
            {"id": "sort", "type": "sort", "name": "Sort", "config": {"field": "v", "direction": "asc"}},
            {"id": "limit", "type": "limit", "name": "Limit", "config": {"max_items": 2}},
        ],
        [("t", "seed"), ("seed", "sort"), ("sort", "limit")],
    )
    runs, _, _ = _run(definition)

    sort_run = next(run for run in runs if run["node_id"] == "sort")
    assert [item["json"]["v"] for item in sort_run["items"]] == [1, 2, 3]
    assert [item["pairedItem"]["item"] for item in sort_run["items"]] == [1, 2, 0]

    # pairedItem is relative to the node's own input, as in n8n.
    limit_run = next(run for run in runs if run["node_id"] == "limit")
    assert [item["pairedItem"]["item"] for item in limit_run["items"]] == [0, 1]


def test_filter_and_split_out_keep_source_links():
    definition = _definition(
        [
            {
                "id": "seed",
                "type": "set",
                "name": "Seed",
                "config": {"values": {"items": [{"v": 1}, {"v": 5}, {"v": 9}]}},
            },
            {"id": "filter", "type": "filter", "name": "Big", "config": {"items": "{{ $json.items }}", "predicate": "{{ $json.v > 2 }}"}},
            {"id": "split", "type": "split_out", "name": "Split", "config": {"field": "tags"}},
        ],
        [("t", "seed"), ("seed", "filter"), ("filter", "split")],
    )
    runs, _, status = _run(definition)

    assert status == "completed"
    filtered = next(run for run in runs if run["node_id"] == "filter")
    assert [item["json"]["v"] for item in filtered["items"]] == [5, 9]
    assert [item["pairedItem"]["item"] for item in filtered["items"]] == [1, 2]


def test_edge_condition_expression_controls_the_branch():
    definition = _definition(
        [
            {"id": "n1", "type": "set", "name": "Only when go", "config": {"values": {"ran": True}}},
        ],
        [("t", "n1")],
    )
    definition["edges"][0]["condition"] = {"when": "success", "expression": "{{ $json.go == true }}"}

    runs, _, _ = _run(definition, {"go": True})
    assert next(run for run in runs if run["node_id"] == "n1")["status"] == "succeeded"

    runs, _, _ = _run(definition, {"go": False})
    n1 = next(run for run in runs if run["node_id"] == "n1")
    assert n1["status"] == "skipped"
    assert n1["reason"] == "BRANCH_NOT_TAKEN"


def test_long_delay_parks_the_run_instead_of_capping():
    definition = _definition(
        [
            {"id": "slow", "type": "delay", "name": "Wait", "config": {"duration_seconds": 3600}},
            {"id": "after", "type": "set", "name": "After", "config": {"values": {"done": True}}},
        ],
        [("t", "slow"), ("slow", "after")],
    )
    runs, output, status = asyncio.run(
        local_engine.execute_definition(definition, {}, parent=RunContext(db=object()))
    )
    assert status == "waiting"
    slow = next(run for run in runs if run["node_id"] == "slow")
    assert slow["reason"] == "WAITING_UNTIL"
    assert slow["outputs"]["resume_at"]
    assert next(run for run in runs if run["node_id"] == "after")["status"] == "skipped"


def test_definition_accepts_run_mode_and_rejects_unknown_values():
    definition = _definition(
        [{"id": "n1", "type": "set", "name": "N", "run_mode": "each", "config": {"values": {"a": 1}}}],
        [("t", "n1")],
    )
    verdict = validate_definition(definition)
    assert verdict.valid, verdict.errors

    definition["nodes"][0]["run_mode"] = "sometimes"
    verdict = validate_definition(definition)
    assert not verdict.valid
    assert any(error.code == "INVALID_RUN_MODE" for error in verdict.errors)


# ---------------------------------------------------------------------------
# Persistence + resume
# ---------------------------------------------------------------------------


def _wait_definition():
    return _definition(
        [
            {"id": "n1", "type": "set", "name": "Seed", "config": {"values": {"x": 1}}},
            {"id": "n2", "type": "delay", "name": "Wait", "config": {"duration_seconds": 3600}},
            {"id": "n3", "type": "set", "name": "After", "config": {"values": {"done": True}}},
        ],
        [("t", "n1"), ("n1", "n2"), ("n2", "n3")],
    )


@pytest.mark.asyncio
async def test_wait_is_persisted_then_resumed(db_session, org_a):
    workflow = Workflow(
        organization_id=org_a.id,
        name="Wait demo",
        slug="wait-demo",
        status=WorkflowStatus.ACTIVE,
    )
    db_session.add(workflow)
    await db_session.flush()
    db_session.add(
        WorkflowVersion(
            workflow_id=workflow.id,
            version="1.0.0",
            schema_version="1.1",
            definition=_wait_definition(),
        )
    )
    await db_session.commit()

    first = await local_engine.run_workflow(
        db_session,
        workflow=workflow,
        organization_id=org_a.id,
        triggered_by=None,
    )
    assert first["status"] == "waiting"

    execution = await db_session.get(WorkflowExecution, UUID(first["execution_id"]))
    assert execution is not None
    assert execution.status == WorkflowExecutionStatus.WAITING
    wait = execution.metadata["wait"]
    assert wait["resume_at"]
    assert wait["definition"]["nodes"][0]["id"] == "n1"
    # n8n-shaped execution data is persisted alongside the run detail.
    assert execution.metadata["execution_data"]["nodes"]["n1"][0]["json"] == {"x": 1}

    # Move the resume time into the past and let the scheduler sweep pick it up.
    execution.metadata = {
        **execution.metadata,
        "wait": {
            **wait,
            "resume_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        },
    }
    await db_session.commit()

    resumed = await resume_due_waits(db_session)
    assert len(resumed) == 1
    assert resumed[0]["status"] == "completed"

    await db_session.refresh(execution)
    assert execution.status == WorkflowExecutionStatus.COMPLETED
    assert "wait" not in execution.metadata
    node_ids = [run["node_id"] for run in execution.metadata["node_runs"]]
    assert node_ids.count("n2") == 1  # the wait node is not duplicated
    after = next(run for run in execution.metadata["node_runs"] if run["node_id"] == "n3")
    assert after["status"] == "succeeded"
    assert execution.metadata["execution_data"]["nodes"]["n3"]
