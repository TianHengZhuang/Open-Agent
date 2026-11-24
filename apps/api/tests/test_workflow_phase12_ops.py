"""Phase 12 production operations: queue mode, webhook lifecycle, execution
data modes, crash journal, metrics and graceful drain."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from openagent.api.v1 import workflow_hooks
from openagent.db.models import (
    ExecutionEvent,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowExecutionTriggerType,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.runtime import crash_journal, lifecycle, local_engine
from openagent.runtime.prometheus import render_workflow_metrics
from openagent.runtime.queue_mode import claim_next, run_worker_once
from openagent.runtime.webhook_registry import find_duplicate, webhook_urls
from openagent.runtime.workflow_ops import dispatch_queued_executions


def _definition(*, settings=None, nodes=None, triggers=None):
    nodes = nodes or [
        {"id": "n1", "type": "set", "name": "N", "config": {"values": {"ok": True}}}
    ]
    return {
        "schema_version": "1.1",
        "triggers": triggers
        or [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": [{"id": f"e{i}", "from": "t", "to": node["id"]} for i, node in enumerate(nodes)],
        "variables": [],
        "settings": settings or {},
    }


async def _save_workflow(db, org, name, slug, definition, *, status=WorkflowStatus.ACTIVE):
    workflow = Workflow(organization_id=org.id, name=name, slug=slug, status=status)
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


# ---------------------------------------------------------------------------
# Queue mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_mode_persists_and_a_worker_completes_the_run(
    db_session, org_a, monkeypatch
):
    monkeypatch.setenv("WORKFLOW_QUEUE_MODE", "true")
    workflow = await _save_workflow(db_session, org_a, "Queued", "queued-mode", _definition())

    result = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "queued"
    assert "worker" in result["message"]

    row = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    assert row.status == WorkflowExecutionStatus.QUEUED
    assert row.metadata["queue_mode"] == "worker"
    assert row.metadata["queued"]["definition"]["schema_version"] == "1.1"

    # The in-process scheduler must never steal a worker's row.
    assert await dispatch_queued_executions(db_session) == []

    done = await run_worker_once(db_session, worker="test-worker")
    assert done is not None
    assert done["status"] == "completed"
    assert done["worker"] == "test-worker"

    await db_session.refresh(row)
    assert row.status == WorkflowExecutionStatus.COMPLETED
    assert any(run["node_id"] == "n1" for run in row.metadata["node_runs"])

    # The queue is empty now.
    assert await claim_next(db_session) is None


@pytest.mark.asyncio
async def test_claim_is_conditional_so_one_run_has_one_worker(db_session, org_a, monkeypatch):
    monkeypatch.setenv("WORKFLOW_QUEUE_MODE", "true")
    workflow = await _save_workflow(db_session, org_a, "Claim", "claim-once", _definition())
    queued = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )

    first = await claim_next(db_session, worker="w1")
    assert first is not None and str(first.id) == queued["execution_id"]
    second = await claim_next(db_session, worker="w2")
    assert second is None


@pytest.mark.asyncio
async def test_shutdown_queues_new_runs_instead_of_starting_them(db_session, org_a):
    lifecycle.reset_lifecycle()
    workflow = await _save_workflow(db_session, org_a, "Drain", "drain-mode", _definition())
    lifecycle.begin_shutdown()
    try:
        result = await local_engine.run_workflow(
            db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
        )
        assert result["status"] == "queued"
        assert "shutting down" in result["message"]
        row = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
        assert row.status == WorkflowExecutionStatus.QUEUED
    finally:
        lifecycle.reset_lifecycle()


@pytest.mark.asyncio
async def test_drain_reports_remaining_runs_and_clears(db_session):
    lifecycle.reset_lifecycle()
    execution_id = UUID("11111111-1111-1111-1111-111111111111")
    lifecycle.begin_run(execution_id)
    assert lifecycle.active_runs() == 1
    assert await lifecycle.wait_for_drain(timeout=0.05) == 1
    lifecycle.end_run(execution_id)
    assert await lifecycle.wait_for_drain(timeout=0.05) == 0
    lifecycle.reset_lifecycle()


# ---------------------------------------------------------------------------
# Execution data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_execution_data_none_keeps_events_but_no_items(db_session, org_a):
    definition = _definition(settings={"save_execution_data": "none"})
    workflow = await _save_workflow(db_session, org_a, "NoData", "no-data", definition)

    result = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    execution = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    assert execution.metadata["save_execution_data"] == "none"
    assert execution.metadata["execution_data"]["nodes"] == {}
    run = next(run for run in execution.metadata["node_runs"] if run["node_id"] == "n1")
    assert run["status"] == "succeeded"
    assert run["items"] == []
    assert "outputs" not in run and "inputs" not in run

    events = (
        await db_session.execute(
            select(ExecutionEvent).where(
                ExecutionEvent.workflow_execution_id == execution.id
            )
        )
    ).scalars().all()
    assert any(event.event_type == "execution.completed" for event in events)


@pytest.mark.asyncio
async def test_save_execution_data_on_error_stores_only_failed_runs(db_session, org_a):
    ok_workflow = await _save_workflow(
        db_session, org_a, "OnError ok", "on-error-ok",
        _definition(settings={"save_execution_data": "on_error"}),
    )
    ok = await local_engine.run_workflow(
        db_session, workflow=ok_workflow, organization_id=org_a.id, triggered_by=None
    )
    ok_row = await db_session.get(WorkflowExecution, UUID(ok["execution_id"]))
    assert ok_row.metadata["execution_data"]["nodes"] == {}

    bad_definition = _definition(
        settings={"save_execution_data": "on_error"},
        nodes=[{"id": "boom", "type": "stop_and_error", "name": "Boom", "config": {"message": "nope"}}],
    )
    bad_workflow = await _save_workflow(db_session, org_a, "OnError bad", "on-error-bad", bad_definition)
    bad = await local_engine.run_workflow(
        db_session, workflow=bad_workflow, organization_id=org_a.id, triggered_by=None
    )
    assert bad["status"] == "failed"
    bad_row = await db_session.get(WorkflowExecution, UUID(bad["execution_id"]))
    assert bad_row.metadata["execution_data"]["nodes"]  # kept for the failure
    failed_run = next(run for run in bad_row.metadata["node_runs"] if run["node_id"] == "boom")
    assert failed_run["error_code"] == "STOP_AND_ERROR"


# ---------------------------------------------------------------------------
# Crash journal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_journal_fails_runs_whose_heartbeat_stopped(db_session, org_a):
    workflow = await _save_workflow(db_session, org_a, "Crash", "crash-journal", _definition())
    now = datetime.now(timezone.utc)
    stale = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=now - timedelta(hours=2),
        metadata={
            "journal": {
                "instance": "dead-host:1:abc",
                "started_at": (now - timedelta(hours=2)).isoformat(),
                "heartbeat": (now - timedelta(hours=2)).isoformat(),
            }
        },
    )
    healthy = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=now,
        metadata={
            "journal": {
                "instance": "live-host:2:def",
                "started_at": now.isoformat(),
                "heartbeat": now.isoformat(),
            }
        },
    )
    db_session.add_all([stale, healthy])
    await db_session.commit()

    crashed = await crash_journal.mark_crashed(db_session, timeout_seconds=60)
    assert [item["execution_id"] for item in crashed] == [str(stale.id)]

    await db_session.refresh(stale)
    await db_session.refresh(healthy)
    assert stale.status == WorkflowExecutionStatus.FAILED
    assert stale.error_code == "EXECUTION_CRASHED"
    assert stale.metadata["journal"]["crashed_at"]
    assert healthy.status == WorkflowExecutionStatus.RUNNING

    events = (
        await db_session.execute(
            select(ExecutionEvent).where(
                ExecutionEvent.workflow_execution_id == stale.id,
                ExecutionEvent.event_type == "execution.crashed",
            )
        )
    ).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_heartbeat_only_bumps_runs_this_process_owns(db_session, org_a):
    workflow = await _save_workflow(db_session, org_a, "Beat", "heartbeat", _definition())
    now = datetime.now(timezone.utc)
    owned = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=now,
        metadata={"journal": {"instance": lifecycle.instance_id(), "heartbeat": "old"}},
    )
    foreign = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=now,
        metadata={"journal": {"instance": "other:9:zzz", "heartbeat": "old"}},
    )
    db_session.add_all([owned, foreign])
    await db_session.commit()

    lifecycle.begin_run(owned.id)
    try:
        assert await crash_journal.heartbeat(db_session) == 1
    finally:
        lifecycle.end_run(owned.id)

    await db_session.refresh(owned)
    await db_session.refresh(foreign)
    assert owned.metadata["journal"]["heartbeat"] != "old"
    assert foreign.metadata["journal"]["heartbeat"] == "old"


# ---------------------------------------------------------------------------
# Webhook lifecycle
# ---------------------------------------------------------------------------


def _hook_definition(path: str):
    return _definition(
        triggers=[
            {"id": "t", "type": "webhook", "name": "Hook", "config": {"path": path}}
        ]
    )


def test_webhook_urls_include_production_and_test():
    urls = webhook_urls(_hook_definition("orders"), origin="https://api.example.com")
    assert urls == [
        {
            "trigger_id": "t",
            "path": "orders",
            "method": "POST",
            "production": "https://api.example.com/api/v1/hooks/orders",
            "test": "https://api.example.com/api/v1/hooks-test/orders",
        }
    ]


@pytest.mark.asyncio
async def test_test_webhook_runs_a_draft_while_production_404s(
    client, db_session, org_a, monkeypatch
):
    await _save_workflow(
        db_session, org_a, "Draft hook", "draft-hook", _hook_definition("phase12-draft"),
        status=WorkflowStatus.DRAFT,
    )
    monkeypatch.setenv("OPENAGENT_WEBHOOK_RATE_LIMIT", "0")
    workflow_hooks.reset_webhook_rate_limits()

    live = await client.post("/api/v1/hooks/phase12-draft", json={"a": 1})
    assert live.status_code == 404
    assert live.json()["error"]["code"] == "WEBHOOK_NOT_FOUND"

    test = await client.post("/api/v1/hooks-test/phase12-draft", json={"a": 1})
    assert test.status_code == 200, test.text
    assert test.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_duplicate_active_webhook_path_is_detected(db_session, org_a):
    first = await _save_workflow(
        db_session, org_a, "First", "first-hook", _hook_definition("shared-path")
    )
    await _save_workflow(
        db_session, org_a, "Second", "second-hook", _hook_definition("shared-path")
    )

    duplicate = await find_duplicate(
        db_session, organization_id=org_a.id, paths=["shared-path"]
    )
    assert duplicate is not None
    assert duplicate["path"] == "shared-path"

    # Excluding the owner leaves only the other workflow; excluding both is clear.
    assert (
        await find_duplicate(
            db_session,
            organization_id=org_a.id,
            paths=["shared-path"],
            exclude_workflow_id=first.id,
        )
    ) is not None
    assert await find_duplicate(db_session, organization_id=org_a.id, paths=["free-path"]) is None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_prometheus_rendering_matches_the_json_snapshot():
    snapshot = {
        "window_hours": 24,
        "totals": {
            "all": 7,
            "by_status": {"completed": 5, "failed": 1, "queued": 1},
            "queued": 1,
            "running": 0,
            "waiting": 0,
        },
        "queue": {"depth": 1, "oldest_queued_seconds": 12.5},
        "runs": {
            "completed": 5,
            "failed": 1,
            "failure_rate": 0.1667,
            "p50_ms": 100.0,
            "p95_ms": 900.0,
            "avg_ms": 200.0,
        },
    }
    text = render_workflow_metrics(snapshot)
    assert 'workflow_executions{status="completed"} 5' in text
    assert 'workflow_executions{status="all"} 7' in text
    assert "workflow_queue_depth 1" in text
    assert "workflow_queue_oldest_seconds 12.5" in text
    assert "workflow_failure_rate 0.1667" in text
    assert 'workflow_run_duration_ms{quantile="0.95"} 900.0' in text
    assert "workflow_run_duration_ms_count 6" in text
    assert text.endswith("\n")


@pytest.mark.asyncio
async def test_metrics_endpoint_requires_the_configured_token(client, monkeypatch):
    monkeypatch.setenv("OPENAGENT_METRICS_TOKEN", "scrape-secret")
    denied = await client.get("/metrics")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "METRICS_TOKEN_INVALID"
