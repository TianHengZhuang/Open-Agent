"""Production webhook entry point for workflows.

n8n exposes every published workflow's Webhook trigger at a stable URL. This
router does the same: ``POST /api/v1/hooks/<path>`` finds the **active**
workflow whose latest version listens on that path and runs it. Drafts never
listen, so publishing is what turns a webhook on.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import time
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.logging import get_logger
from openagent.db.models import Workflow, WorkflowStatus, WorkflowVersion
from openagent.db.session import get_db
from openagent.runtime.binary_store import make_binary_property, max_binary_bytes
from openagent.runtime.local_engine import run_workflow
from openagent.runtime.webhook_registry import normalize_path as _registry_path

logger = get_logger("openagent.api.workflow_hooks")

router = APIRouter(prefix="/hooks", tags=["workflow-hooks"])
test_router = APIRouter(prefix="/hooks-test", tags=["workflow-hooks"])

# Per-organization webhook budget (n8n-style rate limiting). In-process and
# best-effort: several API processes each allow the configured rate.
RATE_WINDOW_SECONDS = 60.0
_DEFAULT_RATE_PER_MINUTE = 120
_DEFAULT_MAX_BODY_BYTES = 1_048_576
_webhook_hits: Dict[str, list[float]] = {}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def max_webhook_body_bytes() -> int:
    return _env_int("OPENAGENT_WEBHOOK_MAX_BODY_BYTES", _DEFAULT_MAX_BODY_BYTES)


def reset_webhook_rate_limits() -> None:
    """Test hook: forget every organization's request window."""
    _webhook_hits.clear()


def _allow_webhook(organization_id: UUID) -> bool:
    """Sliding one-minute window per organization; 0 disables the limit."""
    limit = _env_int("OPENAGENT_WEBHOOK_RATE_LIMIT", _DEFAULT_RATE_PER_MINUTE)
    if limit <= 0:
        return True
    key = str(organization_id)
    now = time.monotonic()
    hits = [stamp for stamp in _webhook_hits.get(key, []) if now - stamp < RATE_WINDOW_SECONDS]
    if len(hits) >= limit:
        _webhook_hits[key] = hits
        return False
    hits.append(now)
    _webhook_hits[key] = hits
    return True


def _normalize_path(value: Any) -> str:
    return _registry_path(value)


async def _latest_version(db: AsyncSession, workflow_id: UUID) -> Optional[WorkflowVersion]:
    result = await db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _find_listener(
    db: AsyncSession, path: str, *, test: bool = False
) -> Tuple[Optional[Workflow], Optional[Dict[str, Any]]]:
    """Return the workflow + webhook trigger listening on ``path``.

    Production (``test=False``) only considers **active** workflows, so
    publishing is what turns a hook on. The test URL (``test=True``, n8n's
test webhook) considers every saved workflow, newest first, so a draft can be
    exercised before it goes live.
    """
    query = select(Workflow).where(Workflow.deleted_at.is_(None))
    if not test:
        query = query.where(Workflow.status == WorkflowStatus.ACTIVE)
    query = query.order_by(Workflow.updated_at.desc())
    result = await db.execute(query)

    matches: Dict[int, Tuple[Workflow, Dict[str, Any]]] = {}
    for workflow in result.scalars().all():
        version = await _latest_version(db, workflow.id)
        if version is None:
            continue
        for trigger in (version.definition or {}).get("triggers") or []:
            if not isinstance(trigger, dict) or trigger.get("type") != "webhook":
                continue
            config = trigger.get("config") or {}
            if _normalize_path(config.get("path")) == path:
                matches[id(workflow)] = (workflow, trigger)
    if not matches:
        return None, None
    if len(matches) > 1:  # publishing blocks new duplicates; pre-existing ones
        logger.warning("Ambiguous webhook path", path=path, matches=len(matches))
    return next(iter(matches.values()))


def _upload_name(request: Request, path: str, content_type: str) -> str:
    """A best-effort filename for a raw upload (filename header wins)."""
    explicit = request.headers.get("x-filename")
    if explicit:
        return str(explicit).strip()[:200]
    base = path.rstrip("/").rsplit("/", 1)[-1] or "upload"
    if not os.path.splitext(base)[1]:
        guess = mimetypes.guess_extension(content_type.split(";", 1)[0]) if content_type else None
        base += guess or ".bin"
    return base[:200]


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


async def _handle(
    request: Request, db: AsyncSession, *, test: bool = False
) -> Dict[str, Any]:
    path = _normalize_path(request.path_params.get("path"))
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Missing webhook path", "code": "WEBHOOK_PATH_REQUIRED"},
        )

    workflow, trigger = await _find_listener(db, path, test=test)
    if workflow is None or trigger is None:
        prefix = "/hooks-test" if test else "/hooks"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": f"No workflow listens on {prefix}/{path}",
                "code": "WEBHOOK_NOT_FOUND",
                "hint": (
                    "Save the workflow to give its test webhook a definition."
                    if test
                    else "Publish the workflow (Publish in the builder) to activate its webhook."
                ),
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

    if not _allow_webhook(workflow.organization_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Webhook rate limit exceeded for this organization",
                "code": "WEBHOOK_RATE_LIMITED",
            },
            headers={"Retry-After": str(int(RATE_WINDOW_SECONDS))},
        )

    cap = max_webhook_body_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(
            status_code=413,
            detail={"error": f"Webhook body exceeds {cap} bytes", "code": "WEBHOOK_BODY_TOO_LARGE"},
        )

    content_type = (request.headers.get("content-type") or "").lower()
    uploads: Dict[str, Any] = {}
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception as exc:  # malformed multipart
            raise HTTPException(
                status_code=400,
                detail={"error": f"Invalid multipart body: {exc}", "code": "WEBHOOK_BODY_INVALID"},
            ) from exc
        fields: Dict[str, Any] = {}
        for key, value in form.items():
            if hasattr(value, "read") and hasattr(value, "filename"):
                content = await value.read()
                if len(content) > min(cap, max_binary_bytes()):
                    raise HTTPException(
                        status_code=413,
                        detail={"error": "Uploaded file exceeds the binary limit", "code": "WEBHOOK_BODY_TOO_LARGE"},
                    )
                name = "data" if not uploads else f"file{len(uploads) + 1}"
                uploads[name] = make_binary_property(
                    content,
                    file_name=str(value.filename or "upload.bin")[:200],
                    mime_type=value.content_type,
                )
            else:
                fields[str(key)] = value
        body: Any = fields or {"raw": ""}
    else:
        raw = await request.body()
        if len(raw) > cap:
            raise HTTPException(
                status_code=413,
                detail={"error": f"Webhook body exceeds {cap} bytes", "code": "WEBHOOK_BODY_TOO_LARGE"},
            )
        is_json = "json" in content_type or not content_type.strip()
        body = None
        if raw and is_json:
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"raw": raw.decode("utf-8", "replace")[:20000]}
        elif raw:
            # Any other content type is a file upload (CSV, image, PDF, ...).
            if len(raw) > min(cap, max_binary_bytes()):
                raise HTTPException(
                    status_code=413,
                    detail={"error": "Uploaded file exceeds the binary limit", "code": "WEBHOOK_BODY_TOO_LARGE"},
                )
            file_name = _upload_name(request, path, content_type)
            uploads["data"] = make_binary_property(
                raw, file_name=file_name, mime_type=content_type.split(";", 1)[0] or None
            )
            body = {
                "raw": raw.decode("utf-8", "replace")[:20000]
                if content_type.startswith(("text/", "application/csv", "application/x-www-form-urlencoded"))
                else "",
            }

    trigger_input: Dict[str, Any] = {
        "method": request.method,
        "path": path,
        "query": dict(request.query_params),
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "body": body if body is not None else {},
    }
    if isinstance(body, dict):
        # n8n exposes parsed JSON directly on the item; keep both shapes.
        trigger_input = {**body, **trigger_input}
    if uploads:
        trigger_input["__binary"] = uploads

    run = await run_workflow(
        db,
        workflow=workflow,
        organization_id=workflow.organization_id,
        triggered_by=None,
        definition=None,
        trigger_input=trigger_input,
    )

    # n8n "Respond to Webhook": a successful responder node defines the HTTP
    # response instead of the execution summary.
    responder = next(
        (
            node_run
            for node_run in run.get("node_runs") or []
            if node_run.get("node_type") == "respond_to_webhook"
            and node_run.get("status") == "succeeded"
        ),
        None,
    )
    if responder is not None:
        outputs = responder.get("outputs") or {}
        payload = outputs.get("body")
        status_code = int(outputs.get("status") or 200)
        headers = {
            str(key): str(value)
            for key, value in (outputs.get("headers") or {}).items()
        }
        return JSONResponse(
            content=payload if isinstance(payload, (dict, list)) else {"body": payload},
            status_code=status_code if 100 <= status_code <= 599 else 200,
            headers=headers,
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
) -> Any:
    """Run the active workflow listening on this path with the JSON body as input."""
    return await _handle(request, db)


@router.get("/{path:path}")
async def workflow_webhook_get(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Run the active workflow listening on this path with query params as input."""
    return await _handle(request, db)


@test_router.post("/{path:path}")
async def workflow_test_webhook_post(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """n8n's test URL: run the saved definition before publishing it."""
    return await _handle(request, db, test=True)


@test_router.get("/{path:path}")
async def workflow_test_webhook_get(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Test URL, GET flavour (query params become the input)."""
    return await _handle(request, db, test=True)
