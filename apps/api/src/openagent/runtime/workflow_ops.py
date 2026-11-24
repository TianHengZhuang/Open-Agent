"""Phase 4 operations for workflow executions.

Per-workflow concurrency, queued dispatch, retention pruning and execution
metrics live here so the API process, the scheduler tick and a worker share one
implementation. Nothing in this module imports the engine at module scope; the
dispatch helper imports it lazily so ``local_engine`` can use the small policy
helpers without a cycle.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.logging import get_logger
from openagent.db.models import (
    ExecutionEvent,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowVersion,
)

logger = get_logger("openagent.workflows.ops")

# 90 days is the platform retention floor for executions (enterprise.py).
DEFAULT_RETENTION_SECONDS = 7_776_000
DEFAULT_KEEP_LAST_EXECUTIONS = 50
TERMINAL_STATUSES = (
    WorkflowExecutionStatus.COMPLETED,
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.CANCELLED,
)


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def concurrency_limit(definition: Dict[str, Any]) -> int:
    """``settings.max_concurrency`` as a slot count (0 = unlimited)."""
    settings = definition.get("settings") or {}
    try:
        value = int(settings.get("max_concurrency"))
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, 32))


def execution_timeout(definition: Dict[str, Any]) -> Optional[int]:
    """``settings.execution_timeout_seconds`` (None = no budget)."""
    settings = definition.get("settings") or {}
    try:
        value = int(settings.get("execution_timeout_seconds"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


SAVE_EXECUTION_DATA_MODES = ("all", "none", "on_error")


def save_execution_data_mode(definition: Dict[str, Any]) -> str:
    """``settings.save_execution_data`` (n8n: all | none | on_error).

    An unknown value falls back to ``all`` so a typo never silently discards
    execution data.
    """
    settings = definition.get("settings") or {}
    value = str(settings.get("save_execution_data") or "all").strip().lower()
    return value if value in SAVE_EXECUTION_DATA_MODES else "all"


async def active_executions(
    db: AsyncSession,
    workflow_id: UUID,
    *,
    exclude_id: Optional[UUID] = None,
) -> int:
    """Runs currently holding a concurrency slot for one workflow."""
    query = select(func.count(WorkflowExecution.id)).where(
        WorkflowExecution.workflow_id == workflow_id,
        WorkflowExecution.status == WorkflowExecutionStatus.RUNNING,
    )
    if exclude_id is not None:
        query = query.where(WorkflowExecution.id != exclude_id)
    return int((await db.execute(query)).scalar_one() or 0)


async def _latest_version(db: AsyncSession, workflow_id: UUID) -> Optional[WorkflowVersion]:
    return (
        await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def dispatch_queued_executions(
    db: AsyncSession,
    *,
    max_runs: int = 10,
) -> List[Dict[str, Any]]:
    """Start queued executions in FIFO order while their workflow has a slot.

    Runs over the workflow's ``max_concurrency`` are persisted as ``queued`` by
    ``run_workflow``; this sweep (called from the scheduler tick) starts them
    instead of dropping them. One run per execution row keeps ordering fair.
    """
    from openagent.runtime.local_engine import run_workflow

    rows = (
        await db.execute(
            select(WorkflowExecution)
            .where(WorkflowExecution.status == WorkflowExecutionStatus.QUEUED)
            .order_by(WorkflowExecution.created_at.asc())
            .limit(max(1, min(max_runs, 100)) * 5)
        )
    ).scalars().all()

    started: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for execution in rows:
        if len(started) >= max_runs:
            break
        # Queue-mode rows belong to a worker; the scheduler must not steal them.
        if (execution.metadata or {}).get("queue_mode") == "worker":
            continue
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
            continue

        queued = (execution.metadata or {}).get("queued") or {}
        definition = queued.get("definition")
        if not isinstance(definition, dict):
            version = await _latest_version(db, workflow.id)
            definition = (version.definition if version else None) or None
        if not isinstance(definition, dict):
            execution.status = WorkflowExecutionStatus.CANCELLED
            execution.completed_at = now
            execution.error_code = "NO_VERSION"
            await db.commit()
            continue

        limit = concurrency_limit(definition)
        if limit and await active_executions(db, workflow.id) >= limit:
            continue  # still full; a later tick starts it

        try:
            run = await run_workflow(
                db,
                workflow=workflow,
                organization_id=execution.organization_id,
                triggered_by=None,
                definition=definition,
                trigger_input=queued.get("trigger_input") or {},
                resume_from=execution,
            )
        except Exception as exc:  # one stuck queue entry must not stop the sweep
            logger.warning(
                "Starting a queued workflow run failed",
                execution_id=str(execution.id),
                error=str(exc),
            )
            continue

        started.append(
            {
                "execution_id": run.get("execution_id"),
                "workflow_id": str(workflow.id),
                "status": run.get("status"),
            }
        )
    return started


async def prune_executions(
    db: AsyncSession,
    *,
    retention_seconds: Optional[int] = None,
    keep_last: Optional[int] = None,
    now: Optional[datetime] = None,
    organization_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Delete terminal executions past retention, keeping the last N per workflow.

    Retention defaults to ``OPENAGENT_WORKFLOW_RETENTION_SECONDS`` (90 days, the
    platform floor). ``0`` disables pruning, matching the cloud retention
    helper's "0 = keep everything" convention. Events belong to their execution
    and are removed with it.
    """
    retention = (
        retention_seconds
        if retention_seconds is not None
        else _env_int("OPENAGENT_WORKFLOW_RETENTION_SECONDS", DEFAULT_RETENTION_SECONDS)
    )
    keep = (
        keep_last
        if keep_last is not None
        else _env_int("OPENAGENT_WORKFLOW_KEEP_LAST", DEFAULT_KEEP_LAST_EXECUTIONS)
    )
    if retention <= 0:
        return {"deleted": 0, "retention_seconds": 0, "reason": "retention disabled"}

    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=retention)
    scope = []
    if organization_id is not None:
        # A chat/API caller prunes only its own organization's history.
        scope.append(WorkflowExecution.organization_id == organization_id)
    workflow_ids = (
        await db.execute(select(WorkflowExecution.workflow_id).where(*scope).distinct())
    ).scalars().all()

    deleted = 0
    for workflow_id in workflow_ids:
        older = (
            await db.execute(
                select(WorkflowExecution)
                .where(WorkflowExecution.workflow_id == workflow_id, *scope)
                .order_by(WorkflowExecution.created_at.desc())
                .offset(max(0, keep))
            )
        ).scalars().all()
        # Compare in Python: SQLite and PostgreSQL disagree on how a naive
        # stored timestamp compares with a timezone-aware bound.
        expired = []
        for row in older:
            if row.status not in TERMINAL_STATUSES or row.completed_at is None:
                continue
            finished = row.completed_at
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            if finished < cutoff:
                expired.append(row.id)
        if not expired:
            continue
        await db.execute(
            delete(ExecutionEvent).where(ExecutionEvent.workflow_execution_id.in_(expired))
        )
        await db.execute(
            delete(WorkflowExecution).where(WorkflowExecution.id.in_(expired))
        )
        deleted += len(expired)

    if deleted:
        await db.commit()
        logger.info("Pruned workflow executions", deleted=deleted, retention_seconds=retention)
    return {
        "deleted": deleted,
        "retention_seconds": retention,
        "keep_last": keep,
        "cutoff": cutoff.isoformat(),
    }


async def cancel_execution_tree(
    db: AsyncSession,
    execution: WorkflowExecution,
    *,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[UUID]:
    """Cancel an execution and every non-terminal descendant.

    A sub-workflow call owns a child execution row (``parent_execution_id``),
    so cancelling the parent must stop the whole tree — an orphan child would
    otherwise keep running after its caller is gone. The caller commits.
    """
    moment = now or datetime.now(timezone.utc)
    cancelled: List[UUID] = []

    if execution.status not in TERMINAL_STATUSES:
        execution.status = WorkflowExecutionStatus.CANCELLED
        execution.completed_at = moment
        if reason:
            execution.metadata = {
                **(execution.metadata or {}),
                "cancel_reason": reason,
            }
        cancelled.append(execution.id)

    frontier = [execution.id]
    for _ in range(50):  # depth guard; the tree is depth-limited upstream
        if not frontier:
            break
        rows = (
            await db.execute(
                select(WorkflowExecution).where(
                    WorkflowExecution.parent_execution_id.in_(frontier),
                    WorkflowExecution.status.not_in(TERMINAL_STATUSES),
                )
            )
        ).scalars().all()
        if not rows:
            break
        frontier = []
        for row in rows:
            row.status = WorkflowExecutionStatus.CANCELLED
            row.completed_at = moment
            row.metadata = {
                **(row.metadata or {}),
                "cancel_reason": reason or "parent cancelled",
            }
            cancelled.append(row.id)
            frontier.append(row.id)

    return cancelled


def _percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


async def execution_metrics(
    db: AsyncSession,
    organization_id: Optional[UUID] = None,
    *,
    window_hours: int = 24,
) -> Dict[str, Any]:
    """Run counts, queue depth, failure rate and latency percentiles."""
    scope = []
    if organization_id is not None:
        scope.append(WorkflowExecution.organization_id == organization_id)

    rows = (
        await db.execute(
            select(WorkflowExecution.status, func.count(WorkflowExecution.id))
            .where(*scope)
            .group_by(WorkflowExecution.status)
        )
    ).all()
    by_status: Dict[str, int] = {}
    for status_value, count in rows:
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        by_status[key] = int(count)
    total = sum(by_status.values())

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=max(1, window_hours))
    recent = (
        await db.execute(
            select(
                WorkflowExecution.status,
                WorkflowExecution.started_at,
                WorkflowExecution.completed_at,
            )
            .where(
                *scope,
                WorkflowExecution.started_at.is_not(None),
                WorkflowExecution.started_at >= window_start,
            )
            .order_by(WorkflowExecution.started_at.desc())
            .limit(5000)
        )
    ).all()

    durations: List[float] = []
    completed = failed = 0
    for status_value, started, completed_at in recent:
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        if key == "completed":
            completed += 1
        elif key == "failed":
            failed += 1
        if started is not None and completed_at is not None:
            try:
                durations.append((completed_at - started).total_seconds() * 1000)
            except TypeError:  # mixed naive/aware rows
                continue

    queued_rows = (
        await db.execute(
            select(WorkflowExecution.created_at)
            .where(*scope, WorkflowExecution.status == WorkflowExecutionStatus.QUEUED)
            .order_by(WorkflowExecution.created_at.asc())
            .limit(1)
        )
    ).scalars().all()
    oldest_queued: Optional[float] = None
    if queued_rows and queued_rows[0] is not None:
        oldest = queued_rows[0]
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        oldest_queued = round((now - oldest).total_seconds(), 1)

    finished = completed + failed
    return {
        "window_hours": window_hours,
        "totals": {
            "all": total,
            "by_status": by_status,
            "queued": by_status.get("queued", 0),
            "running": by_status.get("running", 0),
            "waiting": by_status.get("waiting", 0),
        },
        "queue": {
            "depth": by_status.get("queued", 0),
            "oldest_queued_seconds": oldest_queued,
        },
        "runs": {
            "completed": completed,
            "failed": failed,
            "failure_rate": round(failed / finished, 4) if finished else 0.0,
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
            "avg_ms": round(sum(durations) / len(durations), 1) if durations else None,
        },
    }
