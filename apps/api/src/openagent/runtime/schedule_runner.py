"""Cron runner for workflow Schedule triggers (n8n parity).

n8n's Schedule Trigger fires on a cron timetable without any external caller.
OpenAgent previously only *authored* schedules; this module makes them run.

Design:

* The loop ticks every ``SCHEDULE_TICK_SECONDS`` and evaluates the cron of each
  **active** workflow's latest version.
* A workflow fires at most once per wall-clock minute. The dedupe check reads
  past ``scheduled`` executions, and a per-process key set covers runs whose
  execution row was written with a different trigger type.
* Cron is the standard five-field form (minute hour day-of-month month
  day-of-week) with ``*``, ``?``, lists, ranges, steps and month/weekday
  names. Day-of-month and day-of-week follow classic cron semantics: when both
  are restricted, either may match.
* Disable the loop with ``OPENAGENT_WORKFLOW_SCHEDULER=0``.
* The loop is per API process. Running several API workers does not duplicate
  fires for a given minute because the persisted check catches them; a race
  between two processes in the same instant is possible and acceptable for the
  local-first deployment this engine targets.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.logging import get_logger
from openagent.db.models import (
    Workflow,
    WorkflowExecution,
    WorkflowExecutionTriggerType,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.runtime.local_engine import run_workflow

logger = get_logger("openagent.workflows.schedule")

SCHEDULE_TICK_SECONDS = 20.0

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WEEKDAYS = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# (workflow_id, trigger_id, minute) already fired in this process.
_fired: Set[tuple[str, str, str]] = set()


class CronError(ValueError):
    """Raised for a cron expression the runner cannot understand."""


def _parse_value(token: str, names: Dict[str, int]) -> int:
    low = token.strip().lower()
    if low in names:
        return names[low]
    try:
        return int(low)
    except ValueError as exc:
        raise CronError(f"Invalid cron value {token!r}") from exc


def _field_values(
    field: str,
    low: int,
    high: int,
    names: Optional[Dict[str, int]] = None,
) -> Set[int]:
    names = names or {}
    values: Set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError("Empty cron list entry")
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise CronError(f"Invalid cron step {raw_step!r}") from exc
            if step <= 0:
                raise CronError("Cron step must be positive")
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part:
            raw_start, raw_end = part.split("-", 1)
            start = _parse_value(raw_start, names)
            end = _parse_value(raw_end, names)
        else:
            start = _parse_value(part, names)
            end = high if step > 1 else start
        if end < start:
            raise CronError(f"Cron range {part!r} is reversed")
        for value in range(start, end + 1, step):
            if value < low or value > high:
                raise CronError(f"Cron value {value} out of range {low}..{high}")
            values.add(value)
    return values


def cron_matches(expression: str, when: datetime) -> bool:
    """True when ``when`` (already in the intended timezone) matches ``expression``."""
    fields = (expression or "").split()
    if len(fields) != 5:
        raise CronError("Cron expression must have five fields")

    minutes = _field_values(fields[0], 0, 59)
    hours = _field_values(fields[1], 0, 23)
    days = _field_values(fields[2], 1, 31)
    months = _field_values(fields[3], 1, 12, _MONTHS)
    weekdays = _field_values(fields[4], 0, 7, _WEEKDAYS)

    # cron accepts 7 as Sunday.
    if 7 in weekdays:
        weekdays = (weekdays - {7}) | {0}

    if when.minute not in minutes or when.hour not in hours or when.month not in months:
        return False

    day_restricted = fields[2].strip() not in ("*", "?")
    weekday_restricted = fields[4].strip() not in ("*", "?")
    day_match = when.day in days
    weekday_match = (when.weekday() + 1) % 7 in weekdays

    if day_restricted and weekday_restricted:
        return day_match or weekday_match
    if day_restricted:
        return day_match
    if weekday_restricted:
        return weekday_match
    return True


def schedule_triggers(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    triggers = definition.get("triggers") or []
    return [
        t
        for t in triggers
        if isinstance(t, dict)
        and str(t.get("type") or "") == "schedule"
        and str((t.get("config") or {}).get("cron") or "").strip()
    ]


def trigger_timezone(definition: Dict[str, Any], trigger: Dict[str, Any]) -> str:
    config = trigger.get("config") or {}
    settings = definition.get("settings") or {}
    return str(config.get("timezone") or settings.get("timezone") or "UTC")


def _localize(when: datetime, timezone_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return when.astimezone(ZoneInfo(timezone_name))
    except Exception:  # unknown zone → evaluate in UTC rather than skip
        return when


async def run_due_schedules(session: AsyncSession) -> List[Dict[str, Any]]:
    """Fire every active workflow whose cron matches the current minute."""
    now = datetime.now(timezone.utc)
    minute = now.replace(second=0, microsecond=0)
    minute_key = minute.isoformat()
    fired: List[Dict[str, Any]] = []

    result = await session.execute(
        select(Workflow).where(
            Workflow.status == WorkflowStatus.ACTIVE,
            Workflow.deleted_at.is_(None),
        )
    )
    workflows = list(result.scalars().all())

    for workflow in workflows:
        version: Optional[WorkflowVersion] = (
            await session.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow.id)
                .order_by(WorkflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if version is None:
            continue

        definition = version.definition or {}
        for trigger in schedule_triggers(definition):
            trigger_id = str(trigger.get("id") or "")
            expression = str((trigger.get("config") or {}).get("cron") or "").strip()
            local = _localize(now, trigger_timezone(definition, trigger))
            try:
                due = cron_matches(expression, local)
            except CronError as exc:
                logger.warning(
                    "Skipping invalid cron schedule",
                    workflow_id=str(workflow.id),
                    trigger_id=trigger_id,
                    cron=expression,
                    error=str(exc),
                )
                continue
            if not due:
                continue

            key = (str(workflow.id), trigger_id, minute_key)
            if key in _fired:
                continue
            already = (
                await session.execute(
                    select(func.count(WorkflowExecution.id)).where(
                        WorkflowExecution.workflow_id == workflow.id,
                        WorkflowExecution.trigger_type == WorkflowExecutionTriggerType.SCHEDULED,
                        WorkflowExecution.started_at >= minute,
                    )
                )
            ).scalar_one()
            if already:
                _fired.add(key)
                continue

            _fired.add(key)
            try:
                run = await run_workflow(
                    session,
                    workflow=workflow,
                    organization_id=workflow.organization_id,
                    triggered_by=None,
                    definition=None,
                    trigger_input={
                        "trigger_id": trigger_id,
                        "cron": expression,
                        "scheduled_for": minute_key,
                        "fired_at": now.isoformat(),
                    },
                )
            except Exception as exc:  # one bad workflow must not stop the loop
                logger.warning(
                    "Scheduled workflow run failed",
                    workflow_id=str(workflow.id),
                    trigger_id=trigger_id,
                    error=str(exc),
                )
                continue

            fired.append(
                {
                    "workflow_id": str(workflow.id),
                    "workflow_name": workflow.name,
                    "trigger_id": trigger_id,
                    "cron": expression,
                    "execution_id": run.get("execution_id"),
                    "status": run.get("status"),
                }
            )
            break  # at most one schedule fire per workflow per tick

    return fired


async def start_schedule_loop(tick_seconds: float = SCHEDULE_TICK_SECONDS) -> None:
    """Long-running loop started from the API lifespan (cancelled on shutdown)."""
    if os.environ.get("OPENAGENT_WORKFLOW_SCHEDULER", "1").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    ):
        logger.info("Workflow schedule runner disabled by configuration")
        return

    logger.info("Workflow schedule runner started", tick_seconds=tick_seconds)
    from openagent.db import session as db_session

    while True:
        try:
            maker = db_session.async_session_maker
            if maker is not None:
                async with maker() as session:
                    fired = await run_due_schedules(session)
                if fired:
                    logger.info("Fired scheduled workflows", count=len(fired), fired=fired)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let the loop die
            logger.warning("Workflow schedule tick failed", error=str(exc))
        await asyncio.sleep(tick_seconds)


def next_run_times(
    expression: str,
    timezone_name: str = "UTC",
    *,
    count: int = 3,
    from_time: Optional[datetime] = None,
) -> Sequence[datetime]:
    """Upcoming fire times (minute resolution), used by the API surface."""
    from datetime import timedelta

    base = from_time or datetime.now(timezone.utc)
    cursor = _localize(base, timezone_name).replace(second=0, microsecond=0)
    results: List[datetime] = []
    # A year of minutes is a generous bound for expressions that never match
    # (e.g. "0 0 31 2 *").
    for _ in range(60 * 24 * 366):
        cursor += timedelta(minutes=1)
        if cron_matches(expression, cursor):
            results.append(cursor)
            if len(results) >= count:
                break
    return results


def workflow_schedule_summary(
    workflow_id: UUID,
    definition: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Human-facing schedule rows for a workflow definition."""
    rows: List[Dict[str, Any]] = []
    for trigger in schedule_triggers(definition):
        expression = str((trigger.get("config") or {}).get("cron") or "").strip()
        zone = trigger_timezone(definition, trigger)
        upcoming: List[str] = []
        try:
            upcoming = [t.isoformat() for t in next_run_times(expression, zone, count=3)]
        except CronError:
            upcoming = []
        rows.append(
            {
                "trigger_id": str(trigger.get("id") or ""),
                "name": str(trigger.get("name") or "Schedule"),
                "cron": expression,
                "timezone": zone,
                "next_runs": upcoming,
                "workflow_id": str(workflow_id),
            }
        )
    return rows
