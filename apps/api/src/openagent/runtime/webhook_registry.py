"""Webhook lifecycle helpers (Phase 12).

n8n gives an active workflow a stable production URL and the same trigger a
test URL that works before publishing. This module owns the path rules both
sides share:

* ``/api/v1/hooks/<path>`` — production: only an **active** workflow listens.
* ``/api/v1/hooks-test/<path>`` — test: the latest saved definition listens,
  published or not.
* One path belongs to one active workflow per organization; publishing a
  duplicate is refused instead of silently shadowing an existing hook.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.config import get_settings
from openagent.db.models import Workflow, WorkflowStatus, WorkflowVersion


def normalize_path(value: Any) -> str:
    """A stored webhook path is compared without surrounding slashes."""
    return str(value or "").strip().strip("/")


def webhook_paths(definition: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for trigger in definition.get("triggers") or []:
        if not isinstance(trigger, dict) or trigger.get("type") != "webhook":
            continue
        path = normalize_path((trigger.get("config") or {}).get("path"))
        if path and path not in paths:
            paths.append(path)
    return paths


def base_url() -> str:
    return (get_settings().API_URL or "http://localhost:8000").rstrip("/")


def webhook_urls(definition: Dict[str, Any], *, origin: Optional[str] = None) -> List[Dict[str, Any]]:
    """Resolved production and test URLs for every webhook trigger."""
    root = (origin or base_url()).rstrip("/")
    urls: List[Dict[str, Any]] = []
    for trigger in definition.get("triggers") or []:
        if not isinstance(trigger, dict) or trigger.get("type") != "webhook":
            continue
        config = trigger.get("config") or {}
        path = normalize_path(config.get("path"))
        if not path:
            continue
        urls.append(
            {
                "trigger_id": trigger.get("id"),
                "path": path,
                "method": str(config.get("method") or "POST").upper(),
                "production": f"{root}/api/v1/hooks/{path}",
                "test": f"{root}/api/v1/hooks-test/{path}",
            }
        )
    return urls


async def find_duplicate(
    db: AsyncSession,
    *,
    organization_id: UUID,
    paths: List[str],
    exclude_workflow_id: Optional[UUID] = None,
) -> Optional[Dict[str, Any]]:
    """The active workflow in this organization already owning one of ``paths``."""
    wanted = {normalize_path(path) for path in paths if normalize_path(path)}
    if not wanted:
        return None
    query = select(Workflow).where(
        Workflow.organization_id == organization_id,
        Workflow.status == WorkflowStatus.ACTIVE,
        Workflow.deleted_at.is_(None),
    )
    if exclude_workflow_id is not None:
        query = query.where(Workflow.id != exclude_workflow_id)
    workflows = (await db.execute(query.order_by(Workflow.created_at.asc()))).scalars().all()
    for workflow in workflows:
        version = (
            await db.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow.id)
                .order_by(WorkflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if version is None:
            continue
        for path in webhook_paths(version.definition or {}):
            if path in wanted:
                return {
                    "workflow_id": str(workflow.id),
                    "workflow_name": workflow.name,
                    "path": path,
                }
    return None
