"""Crash journal for workflow executions (Phase 12).

A running execution carries ``metadata["journal"]``: the instance that owns it
and the last heartbeat. The scheduler tick bumps the heartbeat of runs this
process owns; a sweep marks runs whose heartbeat stopped as ``failed`` with
``EXECUTION_CRASHED`` instead of leaving them "running" forever.

The heartbeat is deliberately conservative: a run is only declared crashed
after ``OPENAGENT_WORKFLOW_CRASH_TIMEOUT_SECONDS`` (default 300 s) without a
beat, so a live run in another API instance is never stolen. Executions parked
in ``waiting`` (a Wait node or an approval) are not crashed — the scheduler
resumes timed waits, and a human resumes approvals.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.logging import get_logger
from openagent.db.models import (
    ExecutionEvent,
    WorkflowExecution,
    WorkflowExecutionStatus,
)
from openagent.runtime.lifecycle import active_run_ids, env_float, instance_id

logger = get_logger("openagent.workflows.journal")

JOURNAL_KEY = "journal"
DEFAULT_CRASH_TIMEOUT_SECONDS = 300.0


def crash_timeout_seconds() -> float:
    return env_float(
        "OPENAGENT_WORKFLOW_CRASH_TIMEOUT_SECONDS", DEFAULT_CRASH_TIMEOUT_SECONDS
    )


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def journal_start(execution: WorkflowExecution, *, now: Optional[datetime] = None) -> None:
    """Tag a freshly created execution with its owner process."""
    moment = now or datetime.now(timezone.utc)
    execution.metadata = {
        **(execution.metadata or {}),
        JOURNAL_KEY: {
            "instance": instance_id(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": moment.isoformat(),
            "heartbeat": moment.isoformat(),
        },
    }


async def heartbeat(db: AsyncSession, *, now: Optional[datetime] = None) -> int:
    """Bump the journal heartbeat of the runs this process is executing."""
    owned = set(active_run_ids())
    if not owned:
        return 0
    moment = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(WorkflowExecution).where(
                WorkflowExecution.id.in_(owned),
                WorkflowExecution.status == WorkflowExecutionStatus.RUNNING,
            )
        )
    ).scalars().all()
    bumped = 0
    for execution in rows:
        journal = (execution.metadata or {}).get(JOURNAL_KEY) or {}
        if journal.get("instance") != instance_id():
            continue
        execution.metadata = {
            **(execution.metadata or {}),
            JOURNAL_KEY: {**journal, "heartbeat": moment.isoformat()},
        }
        bumped += 1
    if bumped:
        await db.commit()
    return bumped


async def mark_crashed(
    db: AsyncSession,
    *,
    timeout_seconds: Optional[float] = None,
    now: Optional[datetime] = None,
    organization_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """Fail running executions whose journal heartbeat stopped.

    Returns one record per recovered execution. Commits only when something was
    recovered. A run with no journal at all is judged by ``started_at``.
    """
    moment = now or datetime.now(timezone.utc)
    timeout = crash_timeout_seconds() if timeout_seconds is None else max(0.0, timeout_seconds)
    cutoff = moment - timedelta(seconds=timeout)

    query = select(WorkflowExecution).where(
        WorkflowExecution.status == WorkflowExecutionStatus.RUNNING
    )
    if organization_id is not None:
        query = query.where(WorkflowExecution.organization_id == organization_id)
    rows = (await db.execute(query)).scalars().all()

    mine = set(active_run_ids())
    crashed: List[Dict[str, Any]] = []
    for execution in rows:
        if execution.id in mine:
            continue
        journal = dict((execution.metadata or {}).get(JOURNAL_KEY) or {})
        last = _parse(journal.get("heartbeat")) or _parse(journal.get("started_at"))
        if last is None and execution.started_at is not None:
            last = execution.started_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        if last is None or last > cutoff:
            continue

        execution.status = WorkflowExecutionStatus.FAILED
        execution.completed_at = moment
        execution.error_code = "EXECUTION_CRASHED"
        execution.error_message = (
            "Execution stopped without completing (no heartbeat since "
            f"{last.isoformat()}); the API process running it was lost."
        )
        journal.update({"crashed_at": moment.isoformat(), "last_heartbeat": last.isoformat()})
        execution.metadata = {**(execution.metadata or {}), JOURNAL_KEY: journal}

        sequence = int(
            (
                await db.execute(
                    select(func.coalesce(func.max(ExecutionEvent.sequence), 0)).where(
                        ExecutionEvent.workflow_execution_id == execution.id
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        db.add(
            ExecutionEvent(
                organization_id=execution.organization_id,
                workflow_execution_id=execution.id,
                event_type="execution.crashed",
                sequence=sequence,
                timestamp=moment,
                payload={
                    "instance": journal.get("instance"),
                    "last_heartbeat": last.isoformat(),
                    "timeout_seconds": timeout,
                },
            )
        )
        crashed.append(
            {
                "execution_id": str(execution.id),
                "workflow_id": str(execution.workflow_id),
                "last_heartbeat": last.isoformat(),
            }
        )

    if crashed:
        await db.commit()
        logger.warning("Marked crashed workflow executions", count=len(crashed))
    return crashed
