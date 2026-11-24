"""Workflow queue mode: main process produces, workers consume (Phase 12).

n8n's queue mode moves execution off the API process: the main process persists
the run and hands it to a broker, and a worker executes it. This module keeps
the same contract without a new dependency — the ``workflow_executions`` row is
the queue:

* ``WORKFLOW_QUEUE_MODE=true`` makes ``run_workflow`` persist every new run as
  ``queued`` with ``metadata.queue_mode = "worker"`` instead of executing it.
* A worker claims one run with a conditional ``UPDATE ... WHERE status =
  'queued'``, so two workers can never run the same execution.
* The in-process dispatcher refuses worker rows, so the scheduler cannot steal
  them.

Run a worker with ``python -m openagent.runtime.workflow_worker``. Concurrency
is per workflow: a claim is skipped while ``settings.max_concurrency`` slots of
that workflow are already running.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.logging import get_logger
from openagent.db.models import Workflow, WorkflowExecution, WorkflowExecutionStatus
from openagent.runtime.lifecycle import env_float, instance_id

logger = get_logger("openagent.workflows.queue")

WORKER_MARKER = "worker"
DEFAULT_POLL_SECONDS = 2.0
_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off", "")


def queue_mode_enabled() -> bool:
    raw = os.environ.get("WORKFLOW_QUEUE_MODE", "").strip().lower()
    return raw in _TRUTHY


def poll_seconds() -> float:
    return env_float("OPENAGENT_WORKFLOW_QUEUE_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=0.05)


def worker_id() -> str:
    return os.environ.get("OPENAGENT_WORKFLOW_WORKER_ID", "").strip() or instance_id()


def is_worker_run(execution: WorkflowExecution) -> bool:
    return (execution.metadata or {}).get("queue_mode") == WORKER_MARKER


async def claim_next(
    db: AsyncSession,
    *,
    worker: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[WorkflowExecution]:
    """Atomically claim the oldest runnable worker-mode execution."""
    from openagent.runtime.workflow_ops import active_executions, concurrency_limit

    moment = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(WorkflowExecution)
            .where(WorkflowExecution.status == WorkflowExecutionStatus.QUEUED)
            .order_by(WorkflowExecution.created_at.asc())
            .limit(50)
        )
    ).scalars().all()

    for execution in rows:
        metadata = dict(execution.metadata or {})
        if metadata.get("queue_mode") != WORKER_MARKER:
            continue
        queued = metadata.get("queued") or {}
        definition = queued.get("definition")
        if isinstance(definition, dict):
            limit = concurrency_limit(definition)
            if limit and await active_executions(db, execution.workflow_id) >= limit:
                continue  # the workflow is full; try the next queued run

        claimed = {
            **metadata,
            "claimed_by": worker or worker_id(),
            "claimed_at": moment.isoformat(),
        }
        result = await db.execute(
            update(WorkflowExecution)
            .where(
                WorkflowExecution.id == execution.id,
                WorkflowExecution.status == WorkflowExecutionStatus.QUEUED,
            )
            .values(
                status=WorkflowExecutionStatus.RUNNING,
                started_at=execution.started_at or moment,
                metadata=claimed,
            )
        )
        await db.commit()
        if (result.rowcount or 0) != 1:
            continue  # another worker won the race
        await db.refresh(execution)
        logger.info(
            "Claimed queued workflow execution",
            execution_id=str(execution.id),
            worker=claimed["claimed_by"],
        )
        return execution
    return None


async def run_worker_once(
    db: AsyncSession,
    *,
    worker: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Claim and run one execution. Returns None when the queue is empty."""
    from openagent.runtime.local_engine import run_workflow

    execution = await claim_next(db, worker=worker)
    if execution is None:
        return None

    now = datetime.now(timezone.utc)
    workflow = (
        await db.execute(
            select(Workflow).where(
                Workflow.id == execution.workflow_id,
                Workflow.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if workflow is None:
        execution.status = WorkflowExecutionStatus.CANCELLED
        execution.completed_at = now
        execution.error_code = "WORKFLOW_DELETED"
        await db.commit()
        return {"execution_id": str(execution.id), "status": "cancelled"}

    queued = (execution.metadata or {}).get("queued") or {}
    definition = queued.get("definition")
    try:
        run = await run_workflow(
            db,
            workflow=workflow,
            organization_id=execution.organization_id,
            triggered_by=None,
            definition=definition if isinstance(definition, dict) else None,
            trigger_input=queued.get("trigger_input") or {},
            resume_from=execution,
        )
    except Exception as exc:  # a crash in one run must not kill the worker
        logger.warning(
            "Queued workflow run failed in worker",
            execution_id=str(execution.id),
            error=str(exc),
        )
        await db.rollback()
        row = await db.get(WorkflowExecution, execution.id)
        if row is not None and row.status == WorkflowExecutionStatus.RUNNING:
            row.status = WorkflowExecutionStatus.FAILED
            row.completed_at = datetime.now(timezone.utc)
            row.error_code = "EXECUTION_ENGINE_ERROR"
            row.error_message = str(exc)[:2000]
            await db.commit()
        return {"execution_id": str(execution.id), "status": "failed", "error": str(exc)}

    return {
        "execution_id": run.get("execution_id"),
        "workflow_id": str(workflow.id),
        "status": run.get("status"),
        "worker": worker or worker_id(),
    }


async def run_worker_loop(
    db_factory,
    *,
    max_runs: Optional[int] = None,
    worker: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Poll the queue; ``max_runs`` bounds a test/diagnostic run."""
    done: List[Dict[str, Any]] = []
    while max_runs is None or len(done) < max_runs:
        async with db_factory() as session:
            result = await run_worker_once(session, worker=worker)
        if result is None:
            if max_runs is not None:
                break
            await asyncio.sleep(poll_seconds())
            continue
        done.append(result)
    return done


__all__ = [
    "WORKER_MARKER",
    "claim_next",
    "is_worker_run",
    "poll_seconds",
    "queue_mode_enabled",
    "run_worker_loop",
    "run_worker_once",
    "worker_id",
]
