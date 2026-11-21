"""Koro — automation copilot for any OpenAI-compatible model.

Koro drives the workflow platform through a constrained JSON plan. The
endpoint is configurable per organisation (Ollama or LM Studio locally, or a
hosted API) and stored in ``organizations.settings.copilot``; API keys are
encrypted at rest and never returned. Every action is re-authorised against
the caller's permissions and scoped to their organisation; the model never
receives workflow definitions or credentials, only names, ids and statuses.

When no model is reachable, Koro falls back to deterministic commands
(run / publish / unpublish / list / inspect / create), so workflow control
keeps working fully offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.api.dependencies import get_current_org_context, require_permission
from openagent.api.v1.workflows import (
    _create_version,
    _latest_version,
    _record_audit,
)
from openagent.connectors.crypto import decrypt_secret, encrypt_secret
from openagent.core.config import get_settings
from openagent.db.models import (
    Organization,
    Workflow,
    WorkflowExecution,
    WorkflowStatus,
)
from openagent.db.repositories import WorkflowRepository
from openagent.db.session import get_db
from openagent.services.workflow_definition import slugify, validate_definition
from openagent.services.workspaces import starter_definition

router = APIRouter(prefix="/organizations/{organization_id}/copilot", tags=["koro"])

ACTION_TYPES = ("run", "publish", "unpublish", "list", "inspect", "explain",
                "diagnose", "create", "build", "edit", "none")
MAX_GENERATED_NODES = 40

_SYSTEM_PROMPT = """You are Koro, the automation copilot inside OpenAgent.
You can drive workflows and build complete automations. Reply with ONE JSON object and nothing else:
{"reply": "<one or two short sentences>", "action": {"type": "run|publish|unpublish|list|inspect|explain|diagnose|create|build|edit|none", "workflow": "<workflow name or id>", "name": "<new workflow name>", "definition": <workflow definition>}}

To build a new automation use type "build" and include a full "definition".
To change the open workflow use type "edit" and include the complete replacement "definition".
Workflow definition shape:
{"schema_version": "1.0",
 "triggers": [{"id": "trigger", "type": "manual|schedule|webhook|event", "name": "Start", "config": {}}],
 "nodes": [{"id": "n1", "type": "set|transform|condition|switch|filter|map|sort|limit|remove_duplicates|split_out|delay|webhook|variable|merge|prompt", "name": "...", "config": {}}],
 "edges": [{"id": "e1", "from": "trigger", "to": "n1", "label": "true|false"}],
 "variables": [], "settings": {}}

Rules:
- Triggers: manual config {}, schedule config {"cron": "0 9 * * *"}, webhook config {"path": "/hooks/name"}, event config {"event": "name"}.
- Required configs: set {"values": {...}}; transform {"mapping": {...}} or {"expression": "..."}; condition {"expression": "..."}; switch {"routes": [{"name": "x", "expression": "..."}]}; filter {"items": "{{...}}", "predicate": "..."}; map {"items": "{{...}}", "expression": "..."}; sort {"field": "...", "direction": "asc"}; limit {"max_items": 10}; delay {"duration_seconds": 60}; webhook {"url": "https://...", "method": "POST"}; prompt {"model": "...", "prompt": "..."}; variable {"mode": "set", "name": "x"}; approval {"approvers": ["owner"]}.
- Expressions use {{trigger.field}}, {{nodes.n1.field}} and {{variables.name}}.
- Every node must be reachable from a trigger. A node accepts one incoming edge; use merge for fan-in. Condition edges use label "true" or "false".
- Do not use agent, tool, connector, browser or code nodes: they need existing ids you cannot invent.
- Never include secrets. Keep the workflow under 25 nodes.
- For other requests: run, publish/unpublish, list, inspect (latest run), explain (describe the workflow structure), diagnose (explain why the latest run failed), or create (starter workflow).
- Only reference workflows in the list. If the request is unclear, ask in "reply" and use type "none"."""


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
    api_key_set: bool = False
    local: bool = True
    customized: bool = False


class KoroSettingsResponse(BaseModel):
    base_url: str
    model: str
    provider: str
    local: bool
    api_key_set: bool
    customized: bool
    default_base_url: str
    default_model: str
    models: List[str] = Field(default_factory=list)


class KoroSettingsUpdate(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=500)
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "base_url must be an http(s) URL, e.g. http://127.0.0.1:11434/v1"
            )
        return cleaned


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class KoroConfig:
    """Resolved endpoint for one organisation (stored settings over env defaults)."""
    base_url: str
    model: str
    timeout: float
    api_key: str = ""
    local: bool = True
    provider: str = "OpenAI-compatible"
    customized: bool = False


_PROVIDERS = (
    ("11434", "Ollama"),
    ("1234", "LM Studio"),
    ("api.openai.com", "OpenAI"),
    ("openrouter.ai", "OpenRouter"),
    ("api.groq.com", "Groq"),
    ("api.mistral.ai", "Mistral"),
    ("api.together.xyz", "Together"),
    ("api.deepseek.com", "DeepSeek"),
)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _describe_endpoint(base_url: str) -> tuple[str, bool]:
    host = (urlparse(base_url).hostname or "").lower()
    local = host in _LOCAL_HOSTS or host.endswith(".local")
    for marker, label in _PROVIDERS:
        if marker in base_url.lower():
            return label, local
    return "OpenAI-compatible", local


def _copilot_block(organization: Optional[Organization]) -> Dict[str, Any]:
    if organization is None or not isinstance(organization.settings, dict):
        return {}
    block = organization.settings.get("copilot")
    return block if isinstance(block, dict) else {}


def _resolve_config(organization: Optional[Organization]) -> KoroConfig:
    settings = get_settings()
    block = _copilot_block(organization)
    base_url = str(block.get("base_url") or settings.OPENAGENT_KORO_BASE_URL)
    model = str(block.get("model") or settings.OPENAGENT_KORO_MODEL)
    api_key = settings.OPENAGENT_KORO_API_KEY
    envelope = block.get("api_key_encrypted")
    if envelope and organization is not None:
        try:
            api_key = decrypt_secret(
                settings.ENCRYPTION_KEY, str(envelope),
                associated=f"copilot:{organization.id}",
            )
        except Exception:
            # A rotated/lost encryption key must not break the copilot;
            # the user simply re-enters the key in the settings form.
            api_key = ""
    provider, local = _describe_endpoint(base_url.strip().rstrip("/"))
    return KoroConfig(
        base_url=base_url.strip().rstrip("/"),
        model=model.strip(),
        timeout=settings.OPENAGENT_KORO_TIMEOUT_SECONDS,
        api_key=api_key,
        local=local,
        provider=provider,
        customized=bool(block),
    )


def _auth_headers(config: KoroConfig) -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


async def _list_models(config: KoroConfig, timeout: float = 3.0) -> List[str]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{config.base_url}/models", headers=_auth_headers(config),
            )
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


async def _chat_completion(messages: List[Dict[str, str]], config: KoroConfig) -> str:
    async with httpx.AsyncClient(timeout=config.timeout) as client:
        response = await client.post(
            f"{config.base_url}/chat/completions",
            headers=_auth_headers(config),
            json={
                "model": config.model,
                "messages": messages,
                "temperature": 0.1,
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("The model endpoint returned no choices")
    return str((choices[0].get("message") or {}).get("content") or "")


def _clean_name(value: Any, fallback: str = "Koro Automation") -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip()[:100]
    return name or fallback


def _prepare_definition(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalise a model-generated definition just enough to validate it.

    Model output is untrusted: ids, positions and defaults are repaired here;
    structural validity is still decided by ``validate_definition``.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None

    def objects(key: str) -> List[Dict[str, Any]]:
        value = raw.get(key)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    triggers = objects("triggers")[:4]
    nodes = objects("nodes")
    if len(nodes) > MAX_GENERATED_NODES:
        return None

    used: set[str] = set()
    for index, trigger in enumerate(triggers):
        raw_id = str(trigger.get("id") or f"trigger_{index + 1}")
        trigger_id = raw_id
        suffix = 2
        while trigger_id in used:
            trigger_id = f"{raw_id}_{suffix}"
            suffix += 1
        used.add(trigger_id)
        trigger["id"] = trigger_id
        trigger.setdefault("config", {})
        trigger["name"] = _clean_name(trigger.get("name"),
                                      str(trigger.get("type") or "Start").title())
        position = trigger.get("position")
        if not isinstance(position, dict):
            trigger["position"] = {"x": 80, "y": 120 + index * 160}

    for index, node in enumerate(nodes):
        raw_id = str(node.get("id") or f"n{index + 1}")
        node_id = raw_id
        suffix = 2
        while node_id in used:
            node_id = f"{raw_id}_{suffix}"
            suffix += 1
        used.add(node_id)
        node["id"] = node_id
        node.setdefault("config", {})
        node["name"] = _clean_name(node.get("name"),
                                    str(node.get("type") or "Step").replace("_", " ").title())
        position = node.get("position")
        if not isinstance(position, dict):
            node["position"] = {"x": 320 + (index % 4) * 260,
                                "y": 80 + (index // 4) * 170}

    edges: List[Dict[str, Any]] = []
    for index, edge in enumerate(objects("edges")):
        source, target = edge.get("from"), edge.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        cleaned: Dict[str, Any] = {
            "id": str(edge.get("id") or f"e{index + 1}"),
            "from": source,
            "to": target,
        }
        if isinstance(edge.get("label"), str):
            cleaned["label"] = edge["label"]
        if isinstance(edge.get("condition"), dict):
            cleaned["condition"] = edge["condition"]
        edges.append(cleaned)

    return {
        "schema_version": "1.0",
        "triggers": triggers,
        "nodes": nodes,
        "edges": edges,
        "variables": objects("variables"),
        "settings": raw.get("settings") if isinstance(raw.get("settings"), dict) else {},
    }


def _definition_errors(verdict) -> str:
    messages = [issue.message for issue in verdict.errors[:3]]
    return "I couldn't save that workflow. " + " ".join(messages)


def _workflow_outline(definition: Dict[str, Any]) -> tuple[str, List[str]]:
    """Deterministic, model-free summary of a workflow graph."""
    triggers = [t for t in definition.get("triggers") or [] if isinstance(t, dict)]
    nodes = [n for n in definition.get("nodes") or [] if isinstance(n, dict)]
    edges = definition.get("edges") or []
    trigger_labels = [
        f"{t.get('type')} “{t.get('name')}”" for t in triggers
    ]
    steps = [
        f"{index + 1}. {node.get('name')} [{node.get('type')}]"
        for index, node in enumerate(nodes[:12])
    ]
    summary = (
        f"{len(nodes)} step(s), {len(edges)} connection(s), trigger(s): "
        f"{', '.join(trigger_labels) or 'none'}"
    )
    return summary, steps


def _failure_hint(run: Dict[str, Any]) -> str:
    error = str(run.get("error") or "").lower()
    node_type = str(run.get("node_type") or "")
    if "timeout" in error:
        return "Increase the node timeout or add a retry policy."
    if any(marker in error for marker in ("permission", "forbidden", "401", "403")):
        return "Check the credential or connection permissions used by this node."
    if any(marker in error for marker in ("invalid_config", "missing", "required")):
        return "Open the node and complete its required configuration."
    if node_type == "webhook":
        return "Check the URL, method and headers this node calls."
    return "Open the run panel and inspect this node's input and error."


def _diagnosis_text(execution: WorkflowExecution) -> tuple[str, Dict[str, Any]]:
    """Deterministic explanation of the latest run."""
    metadata = execution.metadata if isinstance(execution.metadata, dict) else {}
    summary = metadata.get("summary") or {}
    runs = [r for r in (metadata.get("node_runs") or []) if isinstance(r, dict)]
    state = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
    duration = summary.get("duration_ms")
    timing = f" in {duration} ms" if isinstance(duration, int) else ""

    failed = [run for run in runs if run.get("status") == "failed" and not run.get("continued")]
    waiting = [run for run in runs if run.get("status") == "waiting"]
    data: Dict[str, Any] = {
        "execution_id": str(execution.id),
        "status": state,
        "summary": summary,
        "failed_nodes": [
            {
                "node_id": run.get("node_id"),
                "node_name": run.get("node_name"),
                "node_type": run.get("node_type"),
                "error": run.get("error"),
                "hint": _failure_hint(run),
            }
            for run in failed
        ],
    }

    if state == "waiting" and waiting:
        names = ", ".join(str(run.get("node_name")) for run in waiting[:3])
        return f"The last run is waiting for approval at {names}. Approve it to continue.", data
    if failed:
        first = failed[0]
        detail = (
            f"The last run failed at “{first.get('node_name')}” "
            f"({first.get('node_type')}): {first.get('error') or 'unknown error'}. "
            f"{_failure_hint(first)}"
        )
        if len(failed) > 1:
            detail += f" {len(failed) - 1} other node(s) also failed."
        return detail, data
    if state == "completed":
        succeeded = summary.get("succeeded", 0)
        return f"The last run completed successfully{timing} ({succeeded} node(s) succeeded).", data
    return f"The last run is {state}{timing}.", data


async def _repair_action(
    action: Dict[str, Any], message: str, config: KoroConfig,
) -> tuple[Dict[str, Any], int]:
    """Feed validation errors back to the model before giving up.

    Small local models rarely get a full workflow definition right on the first
    attempt; two correction passes make builds far more reliable without
    weakening validation.
    """
    wanted = str(action.get("type") or "")
    definition = _prepare_definition(action.get("definition"))
    if definition is not None:
        verdict = validate_definition(definition)
        if verdict.valid:
            action["definition"] = definition
            return action, 0
        errors = _definition_errors(verdict)
    else:
        errors = "The workflow definition was missing or malformed."

    for attempt in range(1, 3):
        repair_messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": message},
            {"role": "assistant", "content": json.dumps(
                {"action": {**action, "definition": definition or action.get("definition")}}
            )[:6000]},
            {"role": "user", "content": (
                f"That workflow definition failed validation: {errors} "
                "Return ONLY a JSON object with the same action type and a complete, "
                "valid corrected definition."
            )},
        ]
        try:
            content = await _chat_completion(repair_messages, config)
            parsed = _extract_json(content)
        except Exception:
            break
        if not parsed:
            continue
        candidate = parsed.get("action") if isinstance(parsed.get("action"), dict) else {}
        if isinstance(parsed.get("definition"), dict) and not candidate.get("definition"):
            candidate["definition"] = parsed["definition"]
        candidate["type"] = wanted
        if not candidate.get("name"):
            candidate["name"] = action.get("name")
        if not candidate.get("workflow"):
            candidate["workflow"] = action.get("workflow")
        action = {**action, **candidate, "type": wanted}
        definition = _prepare_definition(candidate.get("definition"))
        if definition is None:
            errors = "The workflow definition was missing or malformed."
            continue
        action["definition"] = definition
        verdict = validate_definition(definition)
        if verdict.valid:
            return action, attempt
        errors = _definition_errors(verdict)
    return action, 0


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

    if action_type == "build":
        await require_permission("workflow:create")(request, db)
        definition = _prepare_definition(action.get("definition"))
        if definition is None:
            return KoroActionResponse(
                type="build", status="failed",
                detail=("I need a complete workflow definition to build that. "
                        "Describe the trigger and the steps you want."),
            )
        verdict = validate_definition(definition)
        if not verdict.valid:
            return KoroActionResponse(
                type="build", status="failed", detail=_definition_errors(verdict),
            )
        name = _clean_name(action.get("name"), "Koro Automation")
        base_slug = slugify(name) or "koro-automation"
        slug = base_slug
        suffix = 2
        while any(w.slug == slug for w in workflows):
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        repo = WorkflowRepository(db)
        workflow = await repo.create(
            organization_id=organization_id,
            name=name,
            slug=slug,
            description="Built by Koro from the builder copilot. Review it, then run it.",
            status=WorkflowStatus.DRAFT,
            metadata={"tags": ["koro"]},
        )
        await _create_version(db, workflow, definition, user_id)
        await _record_audit(db, request, organization_id, user_id, "workflow.created", workflow,
                            {"version": "v1", "source": "koro-build"})
        await db.commit()
        await db.refresh(workflow)
        return KoroActionResponse(
            type="build",
            status="ok",
            detail=(
                f"Built “{workflow.name}” with {len(definition['nodes'])} node(s) and "
                f"{len(definition['edges'])} connection(s) as a draft. Open it and run it to test."
            ),
            workflow_id=str(workflow.id),
        )

    workflow = _resolve_workflow(workflows, action.get("workflow"), current_id)
    if workflow is None:
        return KoroActionResponse(
            type=action_type,
            status="failed",
            detail="I couldn't match that workflow. Try its exact name.",
        )

    if action_type == "edit":
        await require_permission("workflow:update")(request, db)
        definition = _prepare_definition(action.get("definition"))
        if definition is None:
            return KoroActionResponse(
                type="edit", status="failed", workflow_id=str(workflow.id),
                detail="I need the complete updated workflow definition to make that change.",
            )
        verdict = validate_definition(definition)
        if not verdict.valid:
            return KoroActionResponse(
                type="edit", status="failed", workflow_id=str(workflow.id),
                detail=_definition_errors(verdict),
            )
        version = await _create_version(db, workflow, definition, user_id)
        await _record_audit(db, request, organization_id, user_id,
                            "workflow.version_created", workflow,
                            {"version": version.version, "source": "koro-edit"})
        await db.commit()
        return KoroActionResponse(
            type="edit", status="ok", workflow_id=str(workflow.id),
            detail=(
                f"Updated “{workflow.name}” with draft version {version.version} "
                f"({len(definition['nodes'])} node(s), {len(definition['edges'])} connection(s))."
            ),
        )

    if action_type == "explain":
        await require_permission("workflow:read")(request, db)
        latest = await _latest_version(db, workflow.id)
        if latest is None:
            return KoroActionResponse(
                type="explain", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no saved version to explain yet.",
            )
        summary, steps = _workflow_outline(latest.definition or {})
        detail = f"“{workflow.name}”: {summary}."
        if steps:
            detail += " Steps: " + "; ".join(steps) + "."
        return KoroActionResponse(
            type="explain", status="ok", workflow_id=str(workflow.id),
            detail=detail,
            data={"summary": summary, "steps": steps},
        )

    if action_type == "diagnose":
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
                type="diagnose", status="ok", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has not been run yet, so there is nothing to diagnose.",
            )
        detail, data = _diagnosis_text(execution)
        return KoroActionResponse(
            type="diagnose", status="ok", workflow_id=str(workflow.id),
            detail=f"“{workflow.name}”: {detail}", data=data,
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
        return {
            "reply": (
                "I can build and edit workflows, run, publish, list, inspect, "
                "explain a workflow, or diagnose why the last run failed."
            ),
            "action": None,
        }
    if re.search(r"\b(explain|describe)\b|what does .* (do|mean)|how does .* work", text):
        return {"reply": "Here's what this workflow does.",
                "action": {"type": "explain", "workflow": target()}}
    if re.search(r"\b(why .*fail|debug|diagnose|troubleshoot|what went wrong)\b", text):
        return {"reply": "Checking the latest run.",
                "action": {"type": "diagnose", "workflow": target()}}
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

async def _load_organization(db: AsyncSession, organization_id: UUID) -> Organization:
    organization = await db.get(Organization, organization_id)
    if organization is None or organization.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Organization not found", "code": "NOT_FOUND"},
        )
    return organization


def _settings_response(config: KoroConfig, models: List[str]) -> KoroSettingsResponse:
    settings = get_settings()
    return KoroSettingsResponse(
        base_url=config.base_url,
        model=config.model,
        provider=config.provider,
        local=config.local,
        api_key_set=bool(config.api_key),
        customized=config.customized,
        default_base_url=settings.OPENAGENT_KORO_BASE_URL,
        default_model=settings.OPENAGENT_KORO_MODEL,
        models=models[:50],
    )


@router.get("/status", response_model=KoroStatusResponse)
async def koro_status(
    request: Request,
    organization_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Report whether the configured model is reachable, and how to connect one."""
    await require_permission("workflow:read")(request, db)
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)
    settings = get_settings()
    models = await _list_models(config)
    ready = bool(config.model) and config.model in models

    if ready:
        hint = ""
    elif models:
        hint = (
            f"Endpoint reached, but “{config.model}” is not available there. "
            f"Available: {', '.join(models[:4])}."
        )
    elif config.local and config.provider == "Ollama" and not config.api_key:
        hint = (
            f"Install Ollama, then run: ollama pull {config.model} "
            f"({settings.OPENAGENT_KORO_MODEL_SIZE}). Koro can still run, publish "
            "and create workflows without a model."
        )
    elif config.local:
        hint = (
            f"Start your local model server at {config.base_url}, or point Koro at "
            "any other OpenAI-compatible endpoint in the model settings."
        )
    else:
        hint = (
            "Could not reach the endpoint, or the API key was rejected. Check the "
            "base URL, model name and API key in the model settings."
        )

    return KoroStatusResponse(
        ready=ready,
        provider=config.provider,
        model=config.model,
        model_size=(
            settings.OPENAGENT_KORO_MODEL_SIZE if config.local else "hosted endpoint"
        ),
        base_url=config.base_url,
        models=models[:20],
        install_hint=hint,
        api_key_set=bool(config.api_key),
        local=config.local,
        customized=config.customized,
    )


@router.get("/settings", response_model=KoroSettingsResponse)
async def koro_get_settings(
    request: Request,
    organization_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Current copilot endpoint for this organisation (never returns the key)."""
    await require_permission("workflow:read")(request, db)
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)
    return _settings_response(config, await _list_models(config))


@router.put("/settings", response_model=KoroSettingsResponse)
async def koro_update_settings(
    request: Request,
    organization_id: UUID,
    data: KoroSettingsUpdate,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Point the copilot at any OpenAI-compatible endpoint.

    Stored per organisation in ``organizations.settings.copilot``; the API key
    is encrypted with the platform key before it is written.
    """
    await require_permission("organization:update")(request, db)
    organization = await _load_organization(db, organization_id)

    block = dict(_copilot_block(organization))
    block["base_url"] = data.base_url
    block["model"] = data.model
    if data.clear_api_key:
        block.pop("api_key_encrypted", None)
    elif data.api_key:
        try:
            block["api_key_encrypted"] = encrypt_secret(
                get_settings().ENCRYPTION_KEY,
                data.api_key.strip(),
                associated=f"copilot:{organization.id}",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "Could not store the API key securely",
                        "code": "KEY_STORAGE_FAILED"},
            ) from exc

    organization.settings = {**(organization.settings or {}), "copilot": block}
    await db.commit()
    await db.refresh(organization)

    config = _resolve_config(organization)
    return _settings_response(config, await _list_models(config))


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
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)

    models = await _list_models(config)
    source: Literal["model", "commands"] = "commands"
    model_reply = ""

    # Explicit control commands are handled deterministically first: a 0.5B
    # model is unreliable at picking actions, while the parser is exact.
    plan = _command_plan(data.message, workflows, data.workflow_id)

    if plan is None and models and config.model in models:
        context = [{"name": w.name, "id": str(w.id), "status": str(getattr(w.status, "value", w.status))} for w in workflows[:12]]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT + "\nWorkflows: " + json.dumps(context)},
            *[{"role": m.role, "content": m.content} for m in data.history],
            {"role": "user", "content": data.message},
        ]
        try:
            content = await _chat_completion(messages, config)
            parsed = _extract_json(content)
            if parsed and "reply" in parsed:
                action_payload = parsed.get("action")
                if not isinstance(action_payload, dict):
                    action_payload = {}
                # Models sometimes put the definition beside the action.
                if isinstance(parsed.get("definition"), dict) and not action_payload.get("definition"):
                    action_payload["definition"] = parsed["definition"]
                plan = {"reply": str(parsed.get("reply") or ""), "action": action_payload}
                model_reply = str(parsed.get("reply") or "")
                source = "model"
        except Exception:
            plan = None

    if plan is None:
        wants_build = (
            re.search(r"\b(build|generate|design)\b", data.message.lower())
            and re.search(r"\bworkflow|automation\b", data.message.lower())
        )
        if wants_build:
            return KoroChatResponse(
                reply=(
                    "Connect an OpenAI-compatible model in settings and I'll build the "
                    "whole automation for you. Until then I can create a starter workflow."
                ),
                source="commands",
            )
        names = ", ".join(w.name for w in workflows[:8]) or "none yet"
        reply = (
            f"I can run, publish, build, edit, create, list or inspect workflows. "
            f"Your workflows: {names}. Try “run {workflows[0].name}”."
            if workflows
            else "I can build your first automation — describe what should trigger it and what it should do."
        )
        return KoroChatResponse(reply=reply, source=source if model_reply else "commands")

    action_payload = plan.get("action") or {}
    if not isinstance(action_payload, dict):
        action_payload = {}
    repairs = 0
    if source == "model" and str(action_payload.get("type") or "").lower() in ("build", "edit"):
        action_payload, repairs = await _repair_action(action_payload, data.message, config)
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

    if repairs and action.status == "ok":
        action.data = {**(action.data or {}), "repaired": repairs}

    reply = str(plan.get("reply") or "").strip() or action.detail
    if action.status == "failed":
        reply = action.detail
    elif action.type in ("build", "edit", "explain", "diagnose"):
        # The action detail carries the concrete result (nodes, version, diagnosis).
        reply = action.detail
    return KoroChatResponse(
        reply=reply,
        source=source,
        actions=[action],
        workflow_id=action.workflow_id,
    )
