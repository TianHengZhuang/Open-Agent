"""Phase 4 operations: concurrency queue, dispatch, timeout, pruning, metrics
and webhook rate/body limits."""

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
from openagent.runtime import local_engine
from openagent.runtime.workflow_ops import (
    dispatch_queued_executions,
    execution_metrics,
    prune_executions,
)


def _definition(*, settings=None, nodes=None):
    nodes = nodes or [
        {"id": "n1", "type": "set", "name": "N", "config": {"values": {"ok": True}}}
    ]
    return {
        "schema_version": "1.1",
        "triggers": [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": [{"id": f"e{i}", "from": "t", "to": node["id"]} for i, node in enumerate(nodes)],
        "variables": [],
        "settings": settings or {},
    }


async def _save_workflow(db, org, name, slug, definition, *, status=WorkflowStatus.ACTIVE):
    workflow = Workflow(
        organization_id=org.id,
        name=name,
        slug=slug,
        status=status,
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


def _running_execution(org, workflow):
    return WorkflowExecution(
        organization_id=org.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=datetime.now(timezone.utc),
        metadata={},
    )


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_limit_queues_then_dispatch_starts_the_run(db_session, org_a):
    workflow = await _save_workflow(
        db_session, org_a, "Limited", "limited", _definition(settings={"max_concurrency": 1})
    )
    running = _running_execution(org_a, workflow)
    db_session.add(running)
    await db_session.commit()

    result = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "queued"

    queued_row = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    assert queued_row.status == WorkflowExecutionStatus.QUEUED
    assert queued_row.metadata["queued"]["limit"] == 1
    assert queued_row.metadata["queued"]["definition"]["schema_version"] == "1.1"

    # The slot is still taken, so the dispatcher leaves it queued.
    assert await dispatch_queued_executions(db_session) == []
    await db_session.refresh(queued_row)
    assert queued_row.status == WorkflowExecutionStatus.QUEUED

    running.status = WorkflowExecutionStatus.COMPLETED
    running.completed_at = datetime.now(timezone.utc)
    await db_session.commit()

    started = await dispatch_queued_executions(db_session)
    assert len(started) == 1
    assert started[0]["status"] == "completed"

    await db_session.refresh(queued_row)
    assert queued_row.status == WorkflowExecutionStatus.COMPLETED
    assert queued_row.started_at is not None
    assert any(run["node_id"] == "n1" for run in queued_row.metadata["node_runs"])


@pytest.mark.asyncio
async def test_without_a_limit_a_run_is_not_queued(db_session, org_a):
    workflow = await _save_workflow(db_session, org_a, "Free", "free", _definition())
    db_session.add(_running_execution(org_a, workflow))
    await db_session.commit()

    result = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Execution budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_timeout_fails_the_run(db_session, org_a):
    definition = _definition(
        settings={"execution_timeout_seconds": 1},
        nodes=[{"id": "slow", "type": "delay", "name": "Slow", "config": {"duration_seconds": 3}}],
    )
    workflow = await _save_workflow(db_session, org_a, "Slow", "slow-budget", definition)

    result = await local_engine.run_workflow(
        db_session, workflow=workflow, organization_id=org_a.id, triggered_by=None
    )
    assert result["status"] == "failed"

    execution = await db_session.get(WorkflowExecution, UUID(result["execution_id"]))
    assert execution.status == WorkflowExecutionStatus.FAILED
    assert execution.error_code == "EXECUTION_TIMEOUT"


# ---------------------------------------------------------------------------
# Retention pruning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pruning_deletes_expired_runs_and_keeps_recent_ones(db_session, org_a):
    workflow = await _save_workflow(db_session, org_a, "Prune", "prune", _definition())
    now = datetime.now(timezone.utc)

    old = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.COMPLETED,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=now - timedelta(days=30),
        completed_at=now - timedelta(days=30),
        created_at=now - timedelta(days=30),
        metadata={},
    )
    recent = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.COMPLETED,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=now,
        completed_at=now,
        created_at=now,
        metadata={},
    )
    db_session.add_all([old, recent])
    await db_session.flush()
    db_session.add(
        ExecutionEvent(
            organization_id=org_a.id,
            workflow_execution_id=old.id,
            event_type="execution.completed",
            sequence=1,
            timestamp=now - timedelta(days=30),
            payload={},
        )
    )
    await db_session.commit()
    old_id = old.id

    report = await prune_executions(db_session, retention_seconds=3600, keep_last=1)
    assert report["deleted"] == 1
    assert report["retention_seconds"] == 3600

    assert await db_session.get(WorkflowExecution, old_id) is None
    assert await db_session.get(WorkflowExecution, recent.id) is not None
    events = (
        await db_session.execute(
            select(ExecutionEvent).where(ExecutionEvent.workflow_execution_id == old_id)
        )
    ).scalars().all()
    assert events == []


@pytest.mark.asyncio
async def test_pruning_can_be_disabled(db_session, org_a):
    report = await prune_executions(db_session, retention_seconds=0)
    assert report["deleted"] == 0
    assert report["reason"] == "retention disabled"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_metrics_reports_queue_and_latency(db_session, org_a):
    workflow = await _save_workflow(db_session, org_a, "Metrics", "metrics", _definition())
    now = datetime.now(timezone.utc)
    for duration in (1, 2, 3):
        db_session.add(
            WorkflowExecution(
                organization_id=org_a.id,
                workflow_id=workflow.id,
                status=WorkflowExecutionStatus.COMPLETED,
                trigger_type=WorkflowExecutionTriggerType.MANUAL,
                started_at=now - timedelta(seconds=duration),
                completed_at=now,
                metadata={},
            )
        )
    db_session.add(
        WorkflowExecution(
            organization_id=org_a.id,
            workflow_id=workflow.id,
            status=WorkflowExecutionStatus.QUEUED,
            trigger_type=WorkflowExecutionTriggerType.WEBHOOK,
            metadata={},
            created_at=now - timedelta(seconds=45),
        )
    )
    await db_session.commit()

    metrics = await execution_metrics(db_session, org_a.id)
    assert metrics["totals"]["all"] == 4
    assert metrics["queue"]["depth"] == 1
    assert metrics["queue"]["oldest_queued_seconds"] >= 40
    assert metrics["runs"]["completed"] == 3
    assert metrics["runs"]["p50_ms"] is not None
    assert metrics["runs"]["p95_ms"] is not None


# ---------------------------------------------------------------------------
# Webhook limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_rate_limit_is_per_organization(client, db_session, org_a, monkeypatch):
    definition = {
        "schema_version": "1.1",
        "triggers": [
            {"id": "t", "type": "webhook", "name": "Hook", "config": {"path": "phase4-hook"}}
        ],
        "nodes": [],
        "edges": [],
        "variables": [],
        "settings": {},
    }
    await _save_workflow(db_session, org_a, "Hook", "hook", definition)

    monkeypatch.setenv("OPENAGENT_WEBHOOK_RATE_LIMIT", "2")
    workflow_hooks.reset_webhook_rate_limits()

    first = await client.post("/api/v1/hooks/phase4-hook", json={})
    second = await client.post("/api/v1/hooks/phase4-hook", json={})
    third = await client.post("/api/v1/hooks/phase4-hook", json={})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "WEBHOOK_RATE_LIMITED"


@pytest.mark.asyncio
async def test_webhook_accepts_a_csv_upload_as_binary(client, db_session, org_a, monkeypatch):
    definition = {
        "schema_version": "1.1",
        "triggers": [
            {"id": "t", "type": "webhook", "name": "Hook", "config": {"path": "phase8-upload"}}
        ],
        "nodes": [
            {
                "id": "extract",
                "type": "extract_from_file",
                "name": "Parse upload",
                "config": {"format": "auto"},
            }
        ],
        "edges": [{"id": "e1", "from": "t", "to": "extract"}],
        "variables": [],
        "settings": {},
    }
    await _save_workflow(db_session, org_a, "Upload", "upload", definition)

    monkeypatch.setenv("OPENAGENT_WEBHOOK_RATE_LIMIT", "0")
    workflow_hooks.reset_webhook_rate_limits()

    response = await client.post(
        "/api/v1/hooks/phase8-upload",
        content=b"id,name\n1,Ada\n",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 200, response.text
    runs = response.json()["node_runs"]
    extract = next(run for run in runs if run["node_id"] == "extract")
    assert extract["status"] == "succeeded"
    assert [item["json"] for item in extract["items"]] == [{"id": "1", "name": "Ada"}]


@pytest.mark.asyncio
async def test_webhook_body_cap_rejects_oversized_payloads(
    client, db_session, org_a, monkeypatch
):
    definition = {
        "schema_version": "1.1",
        "triggers": [
            {"id": "t", "type": "webhook", "name": "Hook", "config": {"path": "phase4-body"}}
        ],
        "nodes": [],
        "edges": [],
        "variables": [],
        "settings": {},
    }
    await _save_workflow(db_session, org_a, "Body", "body", definition)

    monkeypatch.setenv("OPENAGENT_WEBHOOK_RATE_LIMIT", "0")
    monkeypatch.setenv("OPENAGENT_WEBHOOK_MAX_BODY_BYTES", "10")
    workflow_hooks.reset_webhook_rate_limits()

    response = await client.post(
        "/api/v1/hooks/phase4-body", json={"message": "this body is too large"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "WEBHOOK_BODY_TOO_LARGE"
