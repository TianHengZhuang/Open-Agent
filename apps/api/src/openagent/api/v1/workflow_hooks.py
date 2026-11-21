"""Production webhook entry point for workflows.

n8n exposes every published workflow's Webhook trigger at a stable URL. This
router does the same: ``POST /api/v1/hooks/<path>`` finds the **active**
workflow whose latest version listens on that path and runs it. Drafts never
listen, so publishing is what turns a webhook on.
"""

from __future__ import annotations

import hmac
import json
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.db.models import Workflow, WorkflowStatus, WorkflowVersion
from openagent.db.session import get_db
from openagent.runtime.local_engine import run_workflow

router = APIRouter(prefix="/hooks", tags=["workflow-hooks"])


def _normalize_path(value: Any) -> str:
    return str(value or "").strip().strip("/")


async def _latest_version(db: AsyncSession, workflow_id: UUID) -> Optional[WorkflowVersion]:
    result = await db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _find_listener(
    db: AsyncSession, path: str
) -> Tuple[Optional[Workflow], Optional[Dict[str, Any]]]:
    """Return the active workflow + webhook trigger listening on ``path``."""
    result = await db.execute(
        select(Workflow).where(
            Workflow.status == WorkflowStatus.ACTIVE,
            Workflow.deleted_at.is_(None),
        )
    )
    for workflow in result.scalars().all():
        version = await _latest_version(db, workflow.id)
        if version is None:
            continue
        for trigger in (version.definition or {}).get("triggers") or []:
            if not isinstance(trigger, dict) or trigger.get("type") != "webhook":
                continue
            config = trigger.get("config") or {}
            if _normalize_path(config.get("path")) == path:
                return workflow, trigger
    return None, None


def _literal_secret(trigger: Dict[str, Any]) -> Optional[str]:
    """A plain shared secret, when the author typed one in.

    Credential references and ``{{...}}`` expressions are not resolvable here
    and are skipped rather than compared literally.
    """
    secret = (trigger.get("config") or {}).get("secret")
    if not isinstance(secret, str):
        return None
    secret = secret.strip()
    if not secret or secret.startswith("credential") or "{{" in secret:
        return None
    return secret


async def _handle(request: Request, db: AsyncSession) -> Dict[str, Any]:
    path = _normalize_path(request.path_params.get("path"))
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Missing webhook path", "code": "WEBHOOK_PATH_REQUIRED"},
        )

    workflow, trigger = await _find_listener(db, path)
    if workflow is None or trigger is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": f"No active workflow listens on /hooks/{path}",
                "code": "WEBHOOK_NOT_FOUND",
                "hint": "Publish the workflow (Publish in the builder) to activate its webhook.",
            },
        )

    expected = _literal_secret(trigger)
    if expected is not None:
        provided = (
            request.headers.get("X-OpenAgent-Secret")
            or request.query_params.get("token")
            or ""
        )
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Invalid webhook secret", "code": "WEBHOOK_SECRET_INVALID"},
            )

    raw = await request.body()
    body: Any = {}
    if raw:
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw.decode("utf-8", "replace")[:20000]}

    trigger_input: Dict[str, Any] = {
        "method": request.method,
        "path": path,
        "query": dict(request.query_params),
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "body": body,
    }
    if isinstance(body, dict):
        # n8n exposes parsed JSON directly on the item; keep both shapes.
        trigger_input = {**body, **trigger_input}

    run = await run_workflow(
        db,
        workflow=workflow,
        organization_id=workflow.organization_id,
        triggered_by=None,
        definition=None,
        trigger_input=trigger_input,
    )
    return {
        "workflow_id": str(workflow.id),
        "workflow_name": workflow.name,
        "trigger_id": trigger.get("id"),
        **run,
    }


@router.post("/{path:path}")
async def workflow_webhook_post(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Run the active workflow listening on this path with the JSON body as input."""
    return await _handle(request, db)


@router.get("/{path:path}")
async def workflow_webhook_get(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Run the active workflow listening on this path with query params as input."""
    return await _handle(request, db)
