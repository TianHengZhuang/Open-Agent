"""Koro — local-first automation copilot.

Koro drives the workflow platform from a small local model (Ollama by default,
``qwen2.5:0.5b-instruct`` at roughly 400 MB quantised) through a constrained
JSON plan. Every action is re-authorised against the caller's permissions and
scoped to their organisation; the model never receives workflow definitions or
credentials, only names, ids and statuses.

When no local model is reachable, Koro falls back to deterministic commands
(run / publish / unpublish / list / inspect / create), so workflow control
keeps working fully offline.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.api.dependencies import get_current_org_context, require_permission
from openagent.api.v1.workflows import (
    _create_version,
    _latest_version,
    _record_audit,
)
from openagent.core.config import get_settings
from openagent.db.models import (
    Workflow,
    WorkflowExecution,
    WorkflowStatus,
)
from openagent.db.repositories import WorkflowRepository
from openagent.db.session import get_db
from openagent.schemas.base import ApiErrorResponse
from openagent.services.workflow_definition import slugify, validate_definition
from openagent.services.workspaces import starter_definition

router = APIRouter(prefix="/organizations/{organization_id}/copilot", tags=["koro"])

ACTION_TYPES = ("run", "publish", "unpublish", "list", "inspect", "create", "none")

_SYSTEM_PROMPT = """You are Koro, the automation copilot inside OpenAgent.
You control the user's workflows. Reply with ONE JSON object and nothing else:
{"reply": "<one or two short sentences>", "action": {"type": "run|publish|unpublish|list|inspect|create|none", "workflow": "<workflow name or id>", "name": "<name for create>"}}
Rules:
- Use "run" to execute a workflow, "publish"/"unpublish" to change activation.
- "inspect" reports the latest run, "list" lists workflows, "create" makes a new starter workflow.
- Only reference workflows in the list. If unsure which one, ask in "reply" and use type "none".
- Never invent ids, endpoints or credentials. Keep replies under 30 words."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class KoroHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class KoroChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    workflow_id: Optional[UUID] = None
    history: List[KoroHistoryMessage] = Field(default_factory=list, max_length=8)
    input: Optional[Dict[str, Any]] = None


class KoroActionResponse(BaseModel):
    type: str
    status: Literal["ok", "failed"]
    detail: str
    workflow_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class KoroChatResponse(BaseModel):
    reply: str
    source: Literal["model", "commands"]
    actions: List[KoroActionResponse] = Field(default_factory=list)
    workflow_id: Optional[str] = None


class KoroStatusResponse(BaseModel):
    ready: bool
    provider: str
    model: str
    model_size: str
    base_url: str
    models: List[str] = Field(default_factory=list)
    install_hint: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _endpoint() -> tuple[str, str, float]:
    settings = get_settings()
    return (
        settings.OPENAGENT_KORO_BASE_URL.rstrip("/"),
        settings.OPENAGENT_KORO_MODEL,
        settings.OPENAGENT_KORO_TIMEOUT_SECONDS,
    )


async def _list_models(base_url: str, timeout: float = 2.0) -> List[str]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{base_url}/models")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    names = []
    for item in models:
        if isinstance(item, dict) and item.get("id"):
            names.append(str(item["id"]))
    return names


async def _chat_completion(messages: List[Dict[str, str]]) -> str:
    base_url, model, timeout = _endpoint()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("Local model returned no choices")
    return str((choices[0].get("message") or {}).get("content") or "")


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _org_workflows(db: AsyncSession, organization_id: UUID) -> List[Workflow]:
    result = await db.execute(
        select(Workflow)
        .where(Workflow.organization_id == organization_id, Workflow.deleted_at.is_(None))
        .order_by(Workflow.updated_at.desc())
    )
    return list(result.scalars().all())


def _resolve_workflow(
    workflows: List[Workflow], reference: Optional[str], current_id: Optional[UUID]
) -> Optional[Workflow]:
    value = (reference or "").strip().lower()
    if value:
        for workflow in workflows:
            if str(workflow.id) == value:
                return workflow
        for workflow in workflows:
            if workflow.name.lower() == value or workflow.slug.lower() == value:
                return workflow
        for workflow in workflows:
            if value in workflow.name.lower() or value in workflow.slug.lower():
                return workflow
    if current_id:
        for workflow in workflows:
            if workflow.id == current_id:
                return workflow
    return None


def _summary_text(result: Dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    pieces = [f"{result.get('status', 'unknown')}"]
    for key in ("succeeded", "failed", "skipped", "waiting"):
        if summary.get(key):
            pieces.append(f"{summary[key]} {key}")
    if summary.get("warnings"):
        pieces.append(f"{summary['warnings']} warning(s)")
    return " · ".join(pieces)


async def _apply_action(
    *,
    db: AsyncSession,
    request: Request,
    organization_id: UUID,
    user_id: Optional[UUID],
    action: Dict[str, Any],
    workflows: List[Workflow],
    current_id: Optional[UUID],
    trigger_input: Optional[Dict[str, Any]],
) -> KoroActionResponse:
    action_type = str(action.get("type") or "none").lower()
    if action_type not in ACTION_TYPES:
        action_type = "none"

    if action_type == "none":
        return KoroActionResponse(type="none", status="ok", detail="No action taken")

    if action_type == "list":
        lines = [f"{w.name} ({w.status.value if hasattr(w.status, 'value') else w.status})" for w in workflows[:12]]
        return KoroActionResponse(
            type="list",
            status="ok",
            detail=f"{len(workflows)} workflow(s): " + ", ".join(lines) if lines else "No workflows yet",
            data={"workflows": [
                {"id": str(w.id), "name": w.name, "status": w.status.value if hasattr(w.status, "value") else str(w.status)}
                for w in workflows
            ]},
        )

    if action_type == "create":
        await require_permission("workflow:create")(request, db)
        raw_name = str(action.get("name") or action.get("workflow") or "").strip()
        name = raw_name or "Koro Workflow"
        name = re.sub(r"\s+", " ", name)[:100]
        slug = slugify(name) or "koro-workflow"
        suffix = 2
        while any(w.slug == slug for w in workflows):
            slug = f"{slugify(name)}-{suffix}"
            suffix += 1
        repo = WorkflowRepository(db)
        workflow = await repo.create(
            organization_id=organization_id,
            name=name,
            slug=slug,
            description="Created by Koro from the builder copilot. Edit it, then run it.",
            status=WorkflowStatus.DRAFT,
            metadata={"tags": ["koro"]},
        )
        definition = starter_definition()
        await _create_version(db, workflow, definition, user_id)
        await _record_audit(db, request, organization_id, user_id, "workflow.created", workflow,
                            {"version": "v1", "source": "koro"})
        await db.commit()
        await db.refresh(workflow)
        return KoroActionResponse(
            type="create",
            status="ok",
            detail=f"Created draft workflow “{workflow.name}” with a trigger, set, condition and two branches.",
            workflow_id=str(workflow.id),
        )

    workflow = _resolve_workflow(workflows, action.get("workflow"), current_id)
    if workflow is None:
        return KoroActionResponse(
            type=action_type,
            status="failed",
            detail="I couldn't match that workflow. Try its exact name.",
        )

    if action_type == "inspect":
        await require_permission("workflow:read")(request, db)
        result = await db.execute(
            select(WorkflowExecution)
            .where(WorkflowExecution.workflow_id == workflow.id)
            .order_by(WorkflowExecution.created_at.desc())
            .limit(1)
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            return KoroActionResponse(
                type="inspect", status="ok", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has not been run yet.",
            )
        node_runs = (execution.metadata or {}).get("node_runs") if isinstance(execution.metadata, dict) else None
        names = []
        if isinstance(node_runs, list):
            names = [f"{run.get('node_name') or run.get('node_id')}: {run.get('status')}" for run in node_runs if isinstance(run, dict)]
        state = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        detail = f"Last run of “{workflow.name}” is {state}."
        if names:
            detail += " " + "; ".join(names[:6])
        return KoroActionResponse(
            type="inspect", status="ok", workflow_id=str(workflow.id),
            detail=detail,
            data={"execution_id": str(execution.id), "status": state, "nodes": names},
        )

    if action_type == "run":
        await require_permission("workflow:execute")(request, db)
        latest = await _latest_version(db, workflow.id)
        if latest is None:
            return KoroActionResponse(type="run", status="failed", detail=f"“{workflow.name}” has no versions to run.")
        from openagent.runtime.local_engine import run_workflow

        result = await run_workflow(
            db,
            workflow=workflow,
            organization_id=organization_id,
            triggered_by=user_id,
            definition=latest.definition or {},
            trigger_input=trigger_input or {},
            version_label=f"koro · {latest.version}",
        )
        summary = _summary_text(result)
        return KoroActionResponse(
            type="run",
            status="ok" if result.get("status") in ("completed", "waiting") else "failed",
            detail=f"Ran “{workflow.name}” — {summary}.",
            workflow_id=str(workflow.id),
            data={"execution_id": result.get("execution_id"), "summary": result.get("summary")},
        )

    if action_type in ("publish", "unpublish"):
        await require_permission("workflow:update")(request, db)
        if action_type == "publish":
            latest = await _latest_version(db, workflow.id)
            if latest is None:
                return KoroActionResponse(type="publish", status="failed", detail=f"“{workflow.name}” has no versions to publish.")
            verdict = validate_definition(latest.definition or {})
            if not verdict.valid:
                return KoroActionResponse(
                    type="publish", status="failed",
                    detail=f"“{workflow.name}” is invalid: {verdict.errors[0].message if verdict.errors else 'fix validation errors'}",
                    workflow_id=str(workflow.id),
                )
            latest.status = "published"
            workflow.status = WorkflowStatus.ACTIVE
            await _record_audit(db, request, organization_id, user_id, "workflow.published", workflow,
                                {"version": latest.version, "source": "koro"})
            detail = f"“{workflow.name}” is active — its webhooks and schedules are live."
        else:
            workflow.status = WorkflowStatus.DRAFT
            await _record_audit(db, request, organization_id, user_id, "workflow.unpublished", workflow,
                                {"source": "koro"})
            detail = f"“{workflow.name}” is inactive."
        await db.commit()
        return KoroActionResponse(type=action_type, status="ok", detail=detail, workflow_id=str(workflow.id))

    return KoroActionResponse(type=action_type, status="failed", detail="Unsupported action")


def _command_plan(message: str, workflows: List[Workflow], current_id: Optional[UUID]) -> Optional[Dict[str, Any]]:
    """Deterministic offline intent parser — Koro keeps working without a model."""
    text = message.lower().strip()

    def target() -> Optional[str]:
        for workflow in workflows:
            if workflow.name.lower() in text or workflow.slug.lower() in text:
                return workflow.name
        if current_id:
            return str(current_id)
        return workflows[0].name if len(workflows) == 1 else None

    if re.search(r"\bwhat can you do\b|\bhelp\b|^koro\?*$", text):
        return {"reply": "I can run, publish, unpublish, create, list and explain workflows. Ask in plain English.", "action": None}
    if re.search(r"\b(unpublish|deactivate|turn off|disable)\b", text):
        return {"reply": "Unpublishing it.", "action": {"type": "unpublish", "workflow": target()}}
    if re.search(r"\b(publish|activate|turn on|go live)\b", text):
        return {"reply": "Publishing it.", "action": {"type": "publish", "workflow": target()}}
    # Inspect before run: "last run" contains the word "run".
    if re.search(r"\b(status|last run|what happened|result|results|log|execution)\b", text):
        return {"reply": "Checking the latest run.", "action": {"type": "inspect", "workflow": target()}}
    if re.search(r"\b(run|test|execute|start|trigger)\b", text):
        return {"reply": "Running it.", "action": {"type": "run", "workflow": target()}}
    if re.search(r"\b(create|new|make|add)\b", text) and re.search(r"\bworkflow|automation\b", text):
        quoted = re.search(r"[\"'“”]([^\"'“”]{2,80})[\"'“”]", message)
        named = re.search(r"\b(?:called|named)\s+([\w \-]{2,80})", message, re.I)
        name = quoted.group(1) if quoted else named.group(1) if named else "Koro Workflow"
        return {"reply": f"Creating “{name}”.", "action": {"type": "create", "name": name.strip()}}
    if re.search(r"\b(list|show|which)\b", text) and re.search(r"\bworkflow|automation\b", text):
        return {"reply": "Here are your workflows.", "action": {"type": "list"}}
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=KoroStatusResponse)
async def koro_status():
    """Report whether the local model is reachable, and how to install it."""
    settings = get_settings()
    base_url, model, _timeout = _endpoint()
    models = await _list_models(base_url)
    ready = model in models
    hint = (
        f"Install Ollama, then run: ollama pull {model} "
        f"({settings.OPENAGENT_KORO_MODEL_SIZE}). Koro can still run, publish and "
        "create workflows without a model."
    )
    if ready and model not in models and models:
        hint = f"Local server is up but “{model}” is not pulled. Run: ollama pull {model}. Available: {', '.join(models[:4])}."
    return KoroStatusResponse(
        ready=ready,
        provider="ollama" if "11434" in base_url else "openai-compatible",
        model=model,
        model_size=settings.OPENAGENT_KORO_MODEL_SIZE,
        base_url=base_url,
        models=models[:20],
        install_hint=hint,
    )


@router.post("/chat", response_model=KoroChatResponse)
async def koro_chat(
    request: Request,
    organization_id: UUID,
    data: KoroChatRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Ask Koro to operate a workflow, locally and offline-first."""
    await require_permission("workflow:read")(request, db)
    workflows = await _org_workflows(db, organization_id)

    base_url, _model, _timeout = _endpoint()
    models = await _list_models(base_url)
    source: Literal["model", "commands"] = "commands"
    model_reply = ""

    # Explicit control commands are handled deterministically first: a 0.5B
    # model is unreliable at picking actions, while the parser is exact.
    plan = _command_plan(data.message, workflows, data.workflow_id)

    if plan is None and models and _model in models:
        context = [{"name": w.name, "id": str(w.id), "status": str(getattr(w.status, "value", w.status))} for w in workflows[:12]]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT + "\nWorkflows: " + json.dumps(context)},
            *[{"role": m.role, "content": m.content} for m in data.history],
            {"role": "user", "content": data.message},
        ]
        try:
            content = await _chat_completion(messages)
            parsed = _extract_json(content)
            if parsed and "reply" in parsed:
                plan = {"reply": str(parsed.get("reply") or ""), "action": parsed.get("action")}
                model_reply = str(parsed.get("reply") or "")
                source = "model"
        except Exception:
            plan = None

    if plan is None:
        names = ", ".join(w.name for w in workflows[:8]) or "none yet"
        reply = (
            f"I can run, publish, unpublish, create, list or inspect workflows. "
            f"Your workflows: {names}. Try “run {workflows[0].name}”."
            if workflows
            else "I can create your first workflow — say “create a workflow called Daily digest”."
        )
        return KoroChatResponse(reply=reply, source=source if model_reply else "commands")

    action_payload = plan.get("action") or {}
    if not isinstance(action_payload, dict):
        action_payload = {}
    try:
        action = await _apply_action(
            db=db,
            request=request,
            organization_id=organization_id,
            user_id=auth_context.user_id,
            action=action_payload,
            workflows=workflows,
            current_id=data.workflow_id,
            trigger_input=data.input,
        )
    except HTTPException as exc:
        detail = exc.detail.get("error") if isinstance(exc.detail, dict) else str(exc.detail)
        action = KoroActionResponse(
            type=str(action_payload.get("type") or "none"),
            status="failed",
            detail=f"You don't have permission: {detail}",
        )

    reply = str(plan.get("reply") or "").strip() or action.detail
    if action.status == "failed":
        reply = action.detail
    return KoroChatResponse(
        reply=reply,
        source=source,
        actions=[action],
        workflow_id=action.workflow_id,
    )
