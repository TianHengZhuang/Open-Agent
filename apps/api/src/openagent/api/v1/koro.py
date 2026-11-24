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

import asyncio
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.api.dependencies import get_current_org_context, require_permission
from openagent.api.v1.koro_tools import (
    ACTION_NAMES,
    TOOL_BY_NAME,
    decode_confirmation,
    issue_confirmation,
    prompt_union,
    tools_payload,
)
from openagent.api.v1.workflows import (
    _create_version,
    _latest_version,
    _record_audit,
)
from openagent.connectors.crypto import decrypt_secret, encrypt_secret
from openagent.core.config import get_settings
from openagent.db.models import (
    Agent,
    AuditLog,
    Conversation,
    ConversationStatus,
    Credential,
    Message,
    MessageRole,
    Organization,
    Tool,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.db.repositories import (
    AgentRepository,
    AgentVersionRepository,
    WorkflowRepository,
    WorkflowVersionRepository,
)
from openagent.db.session import get_db
from openagent.services import local_models
from openagent.services.workflow_definition import slugify, validate_definition
from openagent.services.workspaces import starter_definition

router = APIRouter(prefix="/organizations/{organization_id}/copilot", tags=["koro"])

# Every action the chat can send is declared once in ``koro_tools.registry``;
# the prompt and ``GET /copilot/tools`` read the same table, so a tool cannot
# exist on one surface only (Phase 13).
ACTION_TYPES = ACTION_NAMES + ("none", "batch")
HANDLED_ACTIONS = frozenset(ACTION_NAMES)
MAX_GENERATED_NODES = 40
MAX_CONTEXT_CHARS = 4000


def _expanded_system_prompt() -> str:
    """The prompt with its action union generated from the tool table."""
    return _SYSTEM_PROMPT.replace("<TOOL_ACTIONS>", prompt_union())

_SYSTEM_PROMPT = """You are Koro, the automation copilot inside OpenAgent.
You can drive workflows, build complete automations and create agents. Reply with ONE JSON object and nothing else:
{"reply": "<one or two short sentences>", "action": {"type": "<TOOL_ACTIONS>", "workflow": "<workflow name or id>", "name": "<new workflow name>", "version": "<v3>", "execution_id": "<id>", "definition": <workflow definition>, "variables": {"name": "value"}, "agent": {"name": "...", "description": "...", "agent_type": "chat|workflow|autonomous|assistant", "instructions": "...", "model": "...", "tools": []}}}

To build a new automation use type "build" and include a full "definition".
To change the open workflow use type "edit" and include the complete replacement "definition".
To create an agent use type "create_agent" and include the "agent" object. Use existing tool names from the Tools list when relevant.
Workflow definition shape:
{"schema_version": "1.0",
 "triggers": [{"id": "trigger", "type": "manual|schedule|webhook|event|execute_workflow", "name": "Start", "config": {}}],
 "nodes": [{"id": "n1", "type": "set|transform|condition|switch|filter|map|sort|limit|remove_duplicates|split_out|delay|webhook|variable|merge|prompt|agent|subworkflow", "name": "...", "config": {}}],
 "edges": [{"id": "e1", "from": "trigger", "to": "n1", "label": "true|false"}],
 "variables": [], "settings": {}}

Rules:
- Triggers: manual config {}, schedule config {"cron": "0 9 * * *"}, webhook config {"path": "/hooks/name"}, event config {"event": "name"}.
- Required configs: set {"values": {...}}; transform {"mapping": {...}} or {"expression": "..."}; condition {"expression": "..."}; switch {"routes": [{"name": "x", "expression": "..."}]}; filter {"items": "{{...}}", "predicate": "..."}; map {"items": "{{...}}", "expression": "..."}; sort {"field": "...", "direction": "asc"}; limit {"max_items": 10}; delay {"duration_seconds": 60}; webhook {"url": "https://...", "method": "POST"}; prompt {"model": "...", "prompt": "..."}; agent {"agent_id": "<id from the Agents list>"}; variable {"mode": "set", "name": "x"}; approval {"approvers": ["owner"]}.
- Expressions use n8n syntax: {{ $json.field }} (current item), {{ $node["n1"].json.field }} or {{ $("Node name").first().json.field }} (another node), {{ $vars.name }} (variable), {{ $now.toISODate() }} (date). Method calls, arithmetic, ternaries and optional chaining work, e.g. {{ $json.email.toLowerCase() }}, {{ $json.total > 10 ? "big" : "small" }}, {{ $json.items.map(i => i.json.id).join(",") }}. The older {{trigger.field}}, {{nodes.n1.field}} and {{variables.name}} forms still evaluate and are accepted.
- Every node must be reachable from a trigger. A node accepts one incoming edge; use merge for fan-in. Condition edges use label "true" or "false".
- Only use agent nodes with an agent_id from the Agents list. Do not invent ids for tools, connectors, browser or code nodes; use prompt/set/webhook instead.
- Never include secrets. Keep the workflow under 25 nodes.
- Lifecycle: delete, duplicate, versions (list snapshots), restore (needs "version", e.g. v3), schedule (next fire times).
- Runs: runs (recent executions; optional "workflow", "limit"), inspect (latest run detail), retry (run the latest definition again), cancel (stop a waiting run).
- Platform: list_agents, list_credentials (names and providers only — never values or secrets), variables / set_variable ({"variables": {"name": "value"}} updates the workflow definition).
- Control plane: test_node ({"node": "<node name>"} runs one step and returns its output), update_workflow (rename: {"name": "...", "description": "..."}), set_workflow_setting ({"max_concurrency": 1..32, "execution_timeout_seconds": 1..86400, "error_workflow_id": "<workflow id>"} — 0 clears concurrency; use "clear_error_workflow": true to remove it).
- Node settings: set_node_settings ({"node": "<name>", "on_error": "stop|continue|continueErrorOutput", "retry_on_fail": true, "max_tries": 1..10, "wait_between_tries_ms": 0..60000, "timeout_seconds": 1..300, "always_output_data": true, "execute_once": true, "disabled": true}) — only the fields you name are changed.
- Sub-workflows: link_subworkflow ({"workflow": "<parent name>", "sub_workflow": "<child name>", "node": "<optional Subworkflow node name>"}) points a Subworkflow node at another workflow. Self-links and cycles are refused.
- Static data: set_static_data ({"workflow": "<name>", "scope": "global", "values": {"key": "value"}}) stores values that survive between runs and are read in expressions with {{ $getWorkflowStaticData("global").key }}.
- Coverage: node_coverage ({"query": "slack"}) answers whether a capability is built in (Tier A, local), connector-backed (Tier B), or only reachable over HTTP/MCP/an extension (Tier C). Never claim a Tier C capability is a built-in node; say what to use instead.
- Multi-step requests: use type "batch" with "actions": [ {"type": "..."}, ... ] (max 6). Example: "build it and run it" is one build/edit followed by one run.
- Only reference workflows in the list. If the request is unclear, ask in "reply" and use type "none"."""

# The canonical node registry is appended once so every planning turn sees the
# same type/version surface the backend validates against.
try:  # registry is generated; the API must still boot if it is absent.
    from openagent.registry.node_registry import prompt_catalog as _registry_prompt_catalog

    _SYSTEM_PROMPT = f"{_SYSTEM_PROMPT}\n\n{_registry_prompt_catalog()}"
except Exception:  # pragma: no cover - missing generated registry only
    pass


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
    # Studio assistants send the unsaved canvas so edits start from what the
    # user actually sees, and ask for a preview instead of a saved version.
    definition: Optional[Dict[str, Any]] = None
    preview: bool = False
    # The full-page assistant keeps a transcript per user: pass an existing
    # conversation id, or persist=True to start a new one.
    conversation_id: Optional[UUID] = None
    persist: bool = False
    # Small, app-supplied context (route, open workflow outline, validation,
    # last-run summary). Appended to the system prompt, never persisted.
    context: Optional[Dict[str, Any]] = None
    # Per-chat model override. The organization default is untouched; a
    # different endpoint is only honoured when it is local (see _chat_config).
    model: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=500)


class KoroActionResponse(BaseModel):
    type: str
    status: Literal["ok", "failed", "confirmation_required"]
    detail: str
    workflow_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class KoroChatResponse(BaseModel):
    reply: str
    source: Literal["model", "commands"]
    actions: List[KoroActionResponse] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    conversation_id: Optional[str] = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    source: Optional[str] = None
    actions: List[Dict[str, Any]] = Field(default_factory=list)


class ConversationDetail(ConversationSummary):
    messages: List[ConversationMessage] = Field(default_factory=list)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


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


class ProviderProbeRequest(BaseModel):
    """Ask which models an endpoint serves before saving it as the copilot."""

    base_url: str = Field(min_length=1, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=500)

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


class ProviderProbeResponse(BaseModel):
    ok: bool
    provider: str
    local: bool
    base_url: str
    models: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class LocalModelInfo(BaseModel):
    name: str
    size_bytes: Optional[int] = None
    parameter_size: Optional[str] = None
    quantization: Optional[str] = None
    family: Optional[str] = None
    modified_at: Optional[str | int] = None


class LocalRuntimeInfo(BaseModel):
    id: str
    name: str
    base_url: str
    api: str
    can_pull: bool
    install_url: str = ""
    reachable: bool
    version: Optional[str] = None
    models: List[LocalModelInfo] = Field(default_factory=list)
    error: Optional[str] = None


class RecommendedModelInfo(BaseModel):
    name: str
    label: str
    size: str
    size_bytes: int
    min_ram_gb: int
    description: str
    best_for: str


class LocalModelsResponse(BaseModel):
    runtimes: List[LocalRuntimeInfo]
    recommended: List[RecommendedModelInfo]
    hardware: Dict[str, Any]
    install: Dict[str, Any]
    configured: Dict[str, Any]


class PullModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=500)


class PullJobResponse(BaseModel):
    id: str
    model: str
    base_url: str
    status: Literal["running", "done", "error", "cancelled"]
    progress: float
    completed: int
    total: int
    detail: str
    error: str
    started_at: float
    updated_at: float


class ActivateModelRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)

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


class TestModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=500)


class TestModelResponse(BaseModel):
    ok: bool
    latency_ms: int
    reply: str
    base_url: str
    model: str
    error: Optional[str] = None


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


def _chat_config(
    base: KoroConfig, model: Optional[str], base_url: Optional[str]
) -> KoroConfig:
    """Apply a per-chat model/endpoint override without touching saved defaults.

    The stored API key belongs to the configured endpoint: it is never sent to
    a different host, so a per-chat endpoint must be local. Switching cloud
    providers stays a settings change (where the key is stored encrypted).
    """
    config = base
    if base_url:
        url = base_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "base_url must be an http(s) URL", "code": "KORO_OVERRIDE_INVALID"},
            )
        if url != base.base_url:
            provider, local = _describe_endpoint(url)
            if not local:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": (
                            "A per-chat endpoint must be local. Change the default "
                            "provider in Settings to use another hosted API."
                        ),
                        "code": "KORO_OVERRIDE_NOT_LOCAL",
                    },
                )
            config = replace(
                base, base_url=url, provider=provider, local=local,
                api_key="", customized=True,
            )
    if model and model.strip():
        config = replace(config, model=model.strip())
    return config


def _auth_headers(config: KoroConfig) -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


async def _probe_models(
    config: KoroConfig, timeout: float = 4.0
) -> tuple[List[str], Optional[str]]:
    """List an endpoint's models, keeping the reason it failed."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{config.base_url}/models", headers=_auth_headers(config),
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in (401, 403):
            return [], "The endpoint rejected the API key."
        return [], f"The endpoint answered {status_code}."
    except Exception:
        return [], "Could not reach the endpoint."
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return [], "The endpoint did not return a model list."
    names = []
    for item in models:
        if isinstance(item, dict) and item.get("id"):
            names.append(str(item["id"]))
        elif isinstance(item, str) and item:
            names.append(item)
    if not names:
        return [], "The endpoint has no models installed."
    return names, None


async def _list_models(config: KoroConfig, timeout: float = 3.0) -> List[str]:
    models, _ = await _probe_models(config, timeout=timeout)
    return models


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


def _context_block(context: Optional[Dict[str, Any]]) -> str:
    """Describe the caller's screen for one turn (size-capped, never stored)."""
    if not context:
        return ""
    try:
        encoded = json.dumps(context, default=str)
    except (TypeError, ValueError):
        return ""
    if len(encoded) > MAX_CONTEXT_CHARS:
        encoded = encoded[:MAX_CONTEXT_CHARS] + "…"
    mode = context.get("mode") if isinstance(context, dict) else None
    text = (
        "\nLive context from the app (read-only; it never contains credentials "
        "or workflow parameters): " + encoded
    )
    if mode == "plan":
        text += (
            "\nThe composer is in PLAN mode: answer with a short, ordered plan "
            "and use action type \"none\". Do not build, edit, run or publish "
            "anything until the user switches to build mode."
        )
    return text


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


async def _org_agents(db: AsyncSession, organization_id: UUID) -> List[Agent]:
    result = await db.execute(
        select(Agent)
        .where(Agent.organization_id == organization_id, Agent.deleted_at.is_(None))
        .order_by(Agent.updated_at.desc())
        .limit(30)
    )
    return list(result.scalars().all())


async def _org_tools(db: AsyncSession, organization_id: UUID) -> List[Tool]:
    result = await db.execute(
        select(Tool)
        .where(Tool.organization_id == organization_id, Tool.deleted_at.is_(None))
        .order_by(Tool.updated_at.desc())
        .limit(30)
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


# ---------------------------------------------------------------------------
# Node-level chat mutations
# ---------------------------------------------------------------------------
#
# The canvas is not required to edit a graph. These helpers turn a small,
# typed tool call into a complete definition that goes through the same
# validation and versioning as a model-generated edit, so every chat change is
# still an undoable draft in the builder.

NODE_EDIT_TYPES = (
    "add_node", "update_node", "remove_node", "connect", "disconnect",
    "set_schedule", "set_variable", "set_workflow_setting", "set_node_settings",
    "link_subworkflow",
)


def _definition_nodes(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [node for node in definition.get("nodes") or [] if isinstance(node, dict)]


def _definition_triggers(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [trigger for trigger in definition.get("triggers") or [] if isinstance(trigger, dict)]


def _find_node(definition: Dict[str, Any], reference: Any) -> Optional[Dict[str, Any]]:
    """Find a workflow node by id, exact name, then partial name."""
    value = str(reference or "").strip().lower()
    if not value:
        return None
    nodes = _definition_nodes(definition)
    for node in nodes:
        if str(node.get("id")) == value or str(node.get("name") or "").lower() == value:
            return node
    for node in nodes:
        if value in str(node.get("name") or "").lower():
            return node
    return None


def _find_endpoint(definition: Dict[str, Any], reference: Any) -> Optional[Dict[str, Any]]:
    """Like ``_find_node`` but triggers are valid endpoints for connections."""
    node = _find_node(definition, reference)
    if node is not None:
        return node
    value = str(reference or "").strip().lower()
    for trigger in _definition_triggers(definition):
        if str(trigger.get("id")) == value or str(trigger.get("name") or "").lower() == value:
            return trigger
    return None


async def _subworkflow_cycle(
    db: AsyncSession,
    parent_id: UUID,
    child_id: UUID,
    *,
    max_depth: int = 10,
) -> Optional[List[str]]:
    """Path that would become a cycle if ``parent`` started calling ``child``.

    Follows the child's latest definition through its Subworkflow nodes. A
    ``None`` result means the link is safe; a list is the chain of workflow ids
    that leads back to the parent (or ``parent == child``).
    """
    if parent_id == child_id:
        return [str(parent_id)]

    frontier: List[tuple[UUID, List[str]]] = [(child_id, [str(child_id)])]
    seen: set[UUID] = {child_id}
    while frontier:
        current, path = frontier.pop(0)
        if len(path) > max_depth:
            continue
        version = (
            await db.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == current)
                .order_by(WorkflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        definition = (version.definition if version else None) or {}
        for node in definition.get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("type")) != "subworkflow":
                continue
            raw = (node.get("config") or {}).get("workflow_id")
            try:
                target = UUID(str(raw))
            except (TypeError, ValueError):
                continue
            if target == parent_id:
                return path + [str(parent_id)]
            if target in seen:
                continue
            seen.add(target)
            frontier.append((target, path + [str(target)]))
    return None


def _next_node_id(definition: Dict[str, Any]) -> str:
    used = {str(node.get("id")) for node in _definition_nodes(definition)}
    used |= {str(trigger.get("id")) for trigger in _definition_triggers(definition)}
    index = 1
    while f"n{index}" in used:
        index += 1
    return f"n{index}"


def _terminal_endpoint_id(definition: Dict[str, Any]) -> Optional[str]:
    """The end of the graph a new step should follow."""
    nodes = _definition_nodes(definition)
    if not nodes:
        triggers = _definition_triggers(definition)
        return str(triggers[0].get("id")) if triggers else None
    outgoing = {
        str(edge.get("from"))
        for edge in definition.get("edges") or []
        if isinstance(edge, dict)
    }
    for node in reversed(nodes):
        if str(node.get("id")) not in outgoing:
            return str(node.get("id"))
    return str(nodes[-1].get("id"))


def _node_position(definition: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, int]:
    position = payload.get("position")
    if isinstance(position, dict) and "x" in position and "y" in position:
        try:
            return {"x": int(position["x"]), "y": int(position["y"])}
        except (TypeError, ValueError):
            pass
    xs = [
        int(node["position"]["x"])
        for node in _definition_nodes(definition)
        if isinstance(node.get("position"), dict)
        and isinstance(node["position"].get("x"), (int, float))
    ]
    return {"x": (max(xs) + 280) if xs else 380, "y": 220}


def _mutate_definition(
    definition: Optional[Dict[str, Any]], action: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Apply one node/variable/schedule tool call to a definition copy."""
    if not isinstance(definition, dict):
        return None, "Open a workflow first, then I can change its nodes."
    updated = json.loads(json.dumps(definition))  # deep copy; definitions are JSON
    action_type = str(action.get("type") or "").lower()
    payload = action.get("node") if isinstance(action.get("node"), dict) else {}

    if action_type == "add_node":
        node_type = str(
            payload.get("type") or action.get("node_type") or action.get("type_name") or ""
        ).strip()
        if not node_type:
            return None, "Which node type should I add? For example: HTTP, set, condition, prompt."
        existing = _next_node_id(updated)
        node_id = str(payload.get("id") or action.get("node_id") or existing)
        if any(str(node.get("id")) == node_id for node in _definition_nodes(updated)):
            return None, f"A node with id {node_id!r} already exists."
        name = _clean_name(
            payload.get("name") or action.get("name"),
            node_type.replace("_", " ").title(),
        )
        config = payload.get("config") if isinstance(payload.get("config"), dict) else action.get("config")
        node: Dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "name": name,
            "config": config if isinstance(config, dict) else {},
            "position": _node_position(updated, payload or action),
        }
        if payload.get("notes") or action.get("notes"):
            node["notes"] = str(payload.get("notes") or action.get("notes"))[:500]
        # Resolve the anchor before the new node exists: the added node is itself
        # an open terminal, so looking afterwards would connect it to itself.
        connect_after = action.get("after") or action.get("from") or payload.get("after")
        anchor = _find_endpoint(updated, connect_after) if connect_after else None
        if connect_after and anchor is None:
            return None, f"I couldn't find the node {connect_after!r} to add after."
        anchor_id = str(anchor.get("id")) if anchor else _terminal_endpoint_id(updated)
        updated["nodes"] = [*_definition_nodes(updated), node]
        if anchor_id and action.get("connect") is not False:
            edges = [edge for edge in updated.get("edges") or [] if isinstance(edge, dict)]
            edges.append({
                "id": f"e{len(edges) + 1}",
                "from": anchor_id,
                "to": node_id,
                **({"label": str(action.get("label"))[:40]} if action.get("label") else {}),
            })
            updated["edges"] = edges
        return updated, None

    if action_type == "update_node":
        reference = (
            action.get("node_id") or action.get("node")
            if not isinstance(action.get("node"), dict)
            else payload.get("id") or payload.get("name")
        )
        node = _find_node(updated, reference)
        if node is None:
            return None, f"I couldn't find the node {reference!r}."
        new_config = payload.get("config") if isinstance(payload.get("config"), dict) else action.get("config")
        if isinstance(new_config, dict):
            node["config"] = {**(node.get("config") or {}), **new_config}
        new_name = payload.get("name") or action.get("name")
        if new_name:
            node["name"] = _clean_name(new_name, str(node.get("name") or "Node"))
        new_type = payload.get("type") or action.get("node_type")
        if new_type:
            node["type"] = str(new_type)
        return updated, None

    if action_type == "remove_node":
        reference = (
            action.get("node_id") or action.get("node")
            if not isinstance(action.get("node"), dict)
            else payload.get("id") or payload.get("name")
        )
        node = _find_node(updated, reference)
        if node is None:
            return None, f"I couldn't find the node {reference!r}."
        node_id = str(node.get("id"))
        updated["nodes"] = [
            item for item in _definition_nodes(updated) if str(item.get("id")) != node_id
        ]
        updated["edges"] = [
            edge for edge in updated.get("edges") or []
            if isinstance(edge, dict)
            and str(edge.get("from")) != node_id
            and str(edge.get("to")) != node_id
        ]
        return updated, None

    if action_type in ("connect", "disconnect"):
        source_ref = action.get("from") or payload.get("from")
        target_ref = action.get("to") or payload.get("to")
        source = _find_endpoint(updated, source_ref)
        target = _find_endpoint(updated, target_ref)
        if source is None or target is None:
            return None, "I need two existing nodes to connect (for example: connect Trigger to HTTP)."
        source_id, target_id = str(source.get("id")), str(target.get("id"))
        if source_id == target_id:
            return None, "A node cannot connect to itself."
        edges = [edge for edge in updated.get("edges") or [] if isinstance(edge, dict)]
        if action_type == "connect":
            if any(str(edge.get("from")) == source_id and str(edge.get("to")) == target_id for edge in edges):
                return None, f"{source.get('name') or source_id} is already connected to {target.get('name') or target_id}."
            edge: Dict[str, Any] = {
                "id": f"e{len(edges) + 1}",
                "from": source_id,
                "to": target_id,
            }
            if action.get("label"):
                edge["label"] = str(action["label"])[:40]
            if isinstance(action.get("condition"), dict):
                edge["condition"] = action["condition"]
            edges.append(edge)
            updated["edges"] = edges
            return updated, None
        remaining = [
            edge for edge in edges
            if not (str(edge.get("from")) == source_id and str(edge.get("to")) == target_id)
        ]
        if len(remaining) == len(edges):
            return None, "Those two nodes are not connected."
        updated["edges"] = remaining
        return updated, None

    if action_type == "link_subworkflow":
        child_id = str(action.get("sub_workflow_id") or "").strip()
        if not child_id:
            return None, "Which workflow should the sub-workflow step run?"
        node = _find_node(updated, action.get("node")) if action.get("node") else None
        if node is None:
            node = next(
                (
                    candidate
                    for candidate in _definition_nodes(updated)
                    if str(candidate.get("type")) == "subworkflow"
                ),
                None,
            )
        if node is None:
            return None, (
                "Add a Subworkflow node first, then I can point it at a workflow."
            )
        config = node.get("config")
        if not isinstance(config, dict):
            config = {}
            node["config"] = config
        config["workflow_id"] = child_id
        return updated, None

    if action_type == "set_node_settings":
        reference = action.get("node") or action.get("node_id")
        node = _find_node(updated, reference)
        if node is None:
            return None, f"I couldn't find the node {reference!r}."
        changed: List[str] = []
        if action.get("on_error") is not None:
            raw_mode = str(action["on_error"]).strip()
            normalized = raw_mode.lower().replace("_", "").replace(" ", "")
            if normalized == "stop":
                node.pop("on_error", None)
                node.get("config", {}).pop("on_error", None)
                changed.append("error handling reset")
            elif normalized in ("continue", "continueonerror", "continueerroroutput", "continueonerroroutput"):
                node["on_error"] = (
                    "continueErrorOutput" if "output" in normalized else "continue"
                )
                changed.append(f"on_error={node['on_error']}")
            else:
                return None, "on_error must be stop, continue or continueErrorOutput."
        for flag in ("retry_on_fail", "always_output_data", "execute_once", "disabled"):
            if action.get(flag) is not None:
                if bool(action[flag]):
                    node[flag] = True
                else:
                    node.pop(flag, None)
                changed.append(f"{flag}={bool(action[flag])}")
        if action.get("max_tries") is not None:
            try:
                tries = int(action["max_tries"])
            except (TypeError, ValueError):
                return None, "max_tries must be a number 1..10."
            if not 1 <= tries <= 10:
                return None, "max_tries must be 1..10."
            node["max_tries"] = tries
            changed.append(f"max_tries={tries}")
        if action.get("wait_between_tries_ms") is not None:
            try:
                wait_ms = int(action["wait_between_tries_ms"])
            except (TypeError, ValueError):
                return None, "wait_between_tries_ms must be 0..60000."
            if not 0 <= wait_ms <= 60000:
                return None, "wait_between_tries_ms must be 0..60000."
            node["wait_between_tries_ms"] = wait_ms
            changed.append(f"wait_between_tries_ms={wait_ms}")
        if action.get("timeout_seconds") is not None:
            try:
                timeout = float(action["timeout_seconds"])
            except (TypeError, ValueError):
                return None, "timeout_seconds must be a positive number."
            if timeout <= 0:
                return None, "timeout_seconds must be a positive number."
            node["timeout_seconds"] = timeout
            changed.append(f"timeout_seconds={timeout:g}")
        if not changed:
            return None, "Tell me which node setting to change."
        return updated, None

    if action_type == "set_workflow_setting":
        settings = updated.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        changed: List[str] = []
        if action.get("max_concurrency") is not None:
            try:
                concurrency = int(action["max_concurrency"])
            except (TypeError, ValueError):
                return None, "Concurrency must be a number between 1 and 32 (0 removes the limit)."
            if concurrency == 0:
                settings.pop("max_concurrency", None)
                changed.append("concurrency limit removed")
            elif 1 <= concurrency <= 32:
                settings["max_concurrency"] = concurrency
                changed.append(f"concurrency {concurrency}")
            else:
                return None, "Concurrency must be 1..32 (0 removes the limit)."
        if action.get("execution_timeout_seconds") is not None:
            try:
                timeout = int(action["execution_timeout_seconds"])
            except (TypeError, ValueError):
                return None, "Timeout must be whole seconds between 1 and 86400."
            if 1 <= timeout <= 86400:
                settings["execution_timeout_seconds"] = timeout
                changed.append(f"timeout {timeout}s")
            else:
                return None, "Timeout must be 1..86400 seconds."
        if action.get("clear_error_workflow"):
            settings.pop("error_workflow_id", None)
            changed.append("error workflow cleared")
        elif action.get("error_workflow_id"):
            raw_id = str(action["error_workflow_id"]).strip()
            try:
                settings["error_workflow_id"] = str(UUID(raw_id))
            except (TypeError, ValueError):
                return None, f"{raw_id!r} is not a workflow id I can use as the error workflow."
            changed.append("error workflow")
        if not changed:
            return None, "Tell me which setting to change: concurrency, timeout or error workflow."
        updated["settings"] = settings
        return updated, None

    if action_type == "set_schedule":
        cron = str(action.get("cron") or action.get("schedule") or "").strip()
        if len(cron.split()) < 5:
            return None, "Give me a 5-field cron expression, for example: 0 9 * * *."
        trigger = next(
            (item for item in _definition_triggers(updated) if item.get("type") == "schedule"),
            None,
        )
        if trigger is None:
            return None, "This workflow has no schedule trigger. Build or edit it to add one first."
        trigger["config"] = {**(trigger.get("config") or {}), "cron": cron}
        return updated, None

    if action_type == "set_variable":
        variables = action.get("variables")
        if not isinstance(variables, dict) or not variables:
            return None, "Give me the variable name, for example: declare input email as string."
        declared = [
            dict(item) for item in updated.get("variables") or [] if isinstance(item, dict)
        ]
        for raw_name, spec in list(variables.items())[:20]:
            name = str(raw_name).strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                return None, f"{name!r} is not a valid variable name."
            if isinstance(spec, dict):
                value_type = str(spec.get("type") or "string")
                required = bool(spec.get("required"))
            else:
                value_type = str(spec) if str(spec) in ("string", "number", "boolean", "json") else "string"
                required = False
            entry = {"name": name, "type": value_type, "required": required}
            index = next((i for i, item in enumerate(declared) if item.get("name") == name), None)
            if index is None:
                declared.append(entry)
            else:
                declared[index] = {**declared[index], **entry}
        updated["variables"] = declared
        return updated, None

    return None, "Unsupported node edit."


CONFIRMATION_SUMMARIES = {
    "delete": "This deletes “{name}”. Its run history is kept for audit.",
    "restore": "This restores version {version!r} of “{name}” as a new draft.",
    "prune": "This permanently deletes this organization's executions past the retention window.",
}


def _confirmation_request(
    action_type: str,
    action: Dict[str, Any],
    workflows: List[Workflow],
    current_id: Optional[UUID],
) -> KoroActionResponse:
    """Ask for confirmation with a signed token that carries the action."""
    workflow = _resolve_workflow(workflows, action.get("workflow"), current_id)
    label = workflow.name if workflow is not None else str(action.get("workflow") or "this workflow")
    summary = CONFIRMATION_SUMMARIES.get(action_type, "“{name}” would change saved data.")
    summary = summary.format(name=label, version=action.get("version"))
    token = issue_confirmation({**action, "type": action_type})
    return KoroActionResponse(
        type=action_type,
        status="confirmation_required",
        detail=f"{summary} Reply “confirm {token}” to apply it, or anything else to cancel.",
        workflow_id=str(workflow.id) if workflow is not None else None,
        data={
            "requires_confirmation": True,
            "confirmation_token": token,
            "preview": {
                "action": action_type,
                "summary": summary,
                "workflow_id": str(workflow.id) if workflow is not None else None,
                "workflow_name": workflow.name if workflow is not None else None,
            },
        },
    )


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

    # Aliases keep one handler behind two accepted names.
    if action_type == "schedule":
        action_type = "set_schedule"

    # Plan -> confirm -> apply for destructive tools. The token is the action:
    # a confirmation can only replay exactly what it was issued for.
    spec = TOOL_BY_NAME.get(action_type)
    if spec is not None and spec.requires_confirmation:
        token = str(action.get("confirm") or "").strip()
        decoded = decode_confirmation(token) if token else None
        if decoded is None or str(decoded.get("type") or "").lower() != action_type:
            # No token is issued to a caller who may not run the tool at all.
            await require_permission(spec.permission)(request, db)
            return _confirmation_request(action_type, action, workflows, current_id)
        action = {**decoded, "type": action_type}

    if action_type == "none":
        return KoroActionResponse(type="none", status="ok", detail="No action taken")

    if action_type == "batch":
        return KoroActionResponse(
            type="batch", status="failed",
            detail="Batch actions run one at a time in the chat loop.",
        )

    if action_type == "node_coverage":
        await require_permission("workflow:read")(request, db)
        from openagent.registry import coverage as node_coverage

        result = node_coverage.answer(
            str(action.get("query") or action.get("q") or action.get("node") or "")
        )
        return KoroActionResponse(
            type="node_coverage", status="ok",
            detail=str(result["answer"]), data=result,
        )

    if action_type == "webhook_url":
        await require_permission("workflow:read")(request, db)
        from openagent.runtime.webhook_registry import webhook_urls

        workflow = _resolve_workflow(workflows, action.get("workflow"), current_id)
        if workflow is None:
            return KoroActionResponse(
                type="webhook_url", status="failed",
                detail="Which workflow? Name one or open it in the builder.",
            )
        version = (
            await db.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow.id)
                .order_by(WorkflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        urls = webhook_urls((version.definition if version else None) or {})
        if not urls:
            return KoroActionResponse(
                type="webhook_url", status="ok",
                detail=f"“{workflow.name}” has no Webhook trigger, so it has no URL.",
                data={"workflow_id": str(workflow.id), "webhook_urls": []},
            )
        active = workflow.status == WorkflowStatus.ACTIVE
        first = urls[0]
        detail = (
            f"Production: {first['production']}"
            if active
            else f"Test: {first['test']} (publish the workflow to activate the production URL)"
        )
        if len(urls) > 1:
            detail += f" · {len(urls) - 1} more webhook(s)"
        return KoroActionResponse(
            type="webhook_url", status="ok", detail=detail,
            data={
                "workflow_id": str(workflow.id),
                "workflow_name": workflow.name,
                "active": active,
                "webhook_urls": urls,
            },
        )

    if action_type == "operations_status":
        await require_permission("execution:read")(request, db)
        from openagent.runtime.queue_mode import queue_mode_enabled
        from openagent.runtime.workflow_ops import execution_metrics

        snapshot = await execution_metrics(db, organization_id)
        totals = snapshot.get("totals") or {}
        runs = snapshot.get("runs") or {}
        queue = snapshot.get("queue") or {}
        detail = (
            f"{totals.get('running', 0)} running · {queue.get('depth', 0)} queued · "
            f"failure rate {float(runs.get('failure_rate') or 0.0):.0%} · "
            f"{'queue mode' if queue_mode_enabled() else 'in-process'}"
        )
        return KoroActionResponse(
            type="operations_status", status="ok", detail=detail,
            data={**snapshot, "queue_mode": queue_mode_enabled()},
        )

    if action_type == "prune":
        await require_permission("workflow:update")(request, db)
        from openagent.runtime.workflow_ops import prune_executions

        retention = action.get("retention_seconds")
        try:
            seconds = int(retention) if retention is not None else None
        except (TypeError, ValueError):
            seconds = None
        keep = action.get("keep_last")
        try:
            keep_last = int(keep) if keep is not None else None
        except (TypeError, ValueError):
            keep_last = None
        report = await prune_executions(
            db,
            retention_seconds=seconds,
            keep_last=keep_last,
            organization_id=organization_id,
        )
        return KoroActionResponse(
            type="prune", status="ok",
            detail=f"Pruned {report.get('deleted', 0)} execution(s) past retention.",
            data=report,
        )

    if action_type == "health":
        await require_permission("workflow:read")(request, db)
        from openagent.control.service import get_control_service

        snapshot = get_control_service().health_snapshot()
        return KoroActionResponse(
            type="health", status="ok",
            detail=f"Platform health: {snapshot.get('status') or 'unknown'}.",
            data=snapshot,
        )

    if action_type == "open_page":
        await require_permission("workflow:read")(request, db)
        page = str(action.get("page") or action.get("surface") or "assistant").lower()
        target_workflow: Optional[Workflow] = None
        if page == "workflow":
            target_workflow = _resolve_workflow(
                workflows,
                action.get("workflow") or action.get("workflow_id"),
                current_id,
            )
            if target_workflow is None:
                return KoroActionResponse(
                    type="open_page", status="failed",
                    detail="Which workflow should I open? Name one or open it in the builder.",
                )
            path = f"/workflows/{target_workflow.id}"
            label = f"Open “{target_workflow.name}”"
        elif page == "execution":
            execution_id = str(action.get("execution_id") or "").strip()
            try:
                UUID(execution_id)
            except (TypeError, ValueError):
                return KoroActionResponse(
                    type="open_page", status="failed",
                    detail="Which run should I open? Give me its execution id.",
                )
            path = f"/runs/{execution_id}"
            label = "Open run"
        else:
            path = {
                "assistant": "/assistant",
                "chat": "/assistant",
                "workflows": "/workflows",
                "runs": "/runs",
                "settings": "/settings",
                "account": "/account",
            }.get(page, "")
            if not path:
                return KoroActionResponse(
                    type="open_page", status="failed",
                    detail=(
                        "I can open chat, workflows, runs, settings, account, a workflow "
                        "or a run."
                    ),
                )
            label = {
                "assistant": "Open chat",
                "workflows": "Open workflows",
                "runs": "Open runs",
                "settings": "Open settings",
                "account": "Open account",
            }.get(page, "Open page")
        return KoroActionResponse(
            type="open_page", status="ok",
            workflow_id=str(target_workflow.id) if target_workflow is not None else None,
            detail=f"{label}: {path}",
            data={"path": path, "label": label, "page": page},
        )

    if action_type == "list_agents":
        await require_permission("agent:read")(request, db)
        agents = await _org_agents(db, organization_id)
        detail = (
            f"{len(agents)} agent(s): " + ", ".join(agent.name for agent in agents[:10])
            if agents else "No agents yet. Ask me to create one."
        )
        return KoroActionResponse(
            type="list_agents", status="ok", detail=detail,
            data={"agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "type": str(getattr(agent.agent_type, "value", agent.agent_type)),
                    "status": str(getattr(agent.status, "value", agent.status)),
                }
                for agent in agents[:20]
            ]},
        )

    if action_type == "list_credentials":
        await require_permission("credential:read")(request, db)
        rows = await db.execute(
            select(Credential)
            .where(
                Credential.organization_id == organization_id,
                Credential.deleted_at.is_(None),
            )
            .order_by(Credential.updated_at.desc())
            .limit(30)
        )
        credentials = list(rows.scalars().all())
        # Only metadata leaves the vault; encrypted values are never returned.
        detail = (
            f"{len(credentials)} credential(s): "
            + ", ".join(f"{item.name} ({item.provider})" for item in credentials[:10])
            if credentials else "No credentials yet. Add one in Integrations → Credentials."
        )
        return KoroActionResponse(
            type="list_credentials", status="ok", detail=detail,
            data={"credentials": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "provider": item.provider,
                    "type": str(getattr(item.credential_type, "value", item.credential_type)),
                    "status": str(getattr(item.status, "value", item.status)),
                }
                for item in credentials
            ]},
        )

    if (
        action_type == "runs"
        and not str(action.get("workflow") or "").strip()
        and current_id is None
    ):
        await require_permission("execution:read")(request, db)
        limit = min(max(int(action.get("limit") or 6), 1), 20)
        rows = await db.execute(
            select(WorkflowExecution)
            .where(WorkflowExecution.organization_id == organization_id)
            .order_by(WorkflowExecution.created_at.desc())
            .limit(limit)
        )
        executions = list(rows.scalars().all())
        names = {workflow.id: workflow.name for workflow in workflows}
        detail = (
            f"Latest {len(executions)} run(s): " + "; ".join(
                f"{names.get(execution.workflow_id, 'workflow')} → "
                f"{str(getattr(execution.status, 'value', execution.status))}"
                for execution in executions
            )
            if executions else "No runs yet."
        )
        return KoroActionResponse(
            type="runs", status="ok", detail=detail,
            data={"runs": [
                {
                    "id": str(execution.id),
                    "workflow_id": str(execution.workflow_id),
                    "workflow_name": names.get(execution.workflow_id),
                    "status": str(getattr(execution.status, "value", execution.status)),
                    "created_at": execution.created_at.isoformat() if execution.created_at else None,
                    "error": execution.error_message,
                }
                for execution in executions
            ]},
        )

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

    if action_type == "create_agent":
        await require_permission("agent:create")(request, db)
        payload = action.get("agent") if isinstance(action.get("agent"), dict) else {}
        name = _clean_name(payload.get("name") or action.get("name"), "Koro Agent")
        description = str(payload.get("description") or action.get("description") or "").strip()[:500]
        instructions = str(payload.get("instructions") or action.get("instructions") or description).strip()[:8000]
        agent_type = str(payload.get("agent_type") or action.get("agent_type") or "chat").lower()
        if agent_type not in ("chat", "workflow", "autonomous", "assistant"):
            agent_type = "chat"
        tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
        organization = await _load_organization(db, organization_id)
        configuration = {
            "instructions": instructions,
            "model": str(payload.get("model") or _resolve_config(organization).model),
            "tools": tools,
            "generated_by": "koro",
        }
        base_slug = slugify(name) or "koro-agent"
        slug = base_slug
        suffix = 2
        while True:
            existing = await AgentRepository(db).get_by_slug(organization_id, slug)
            if existing is None:
                break
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        repo = AgentRepository(db)
        agent = await repo.create(
            organization_id=organization_id,
            name=name,
            slug=slug,
            description=description or "Created by Koro from the builder copilot.",
            status="draft",
            agent_type=agent_type,
            metadata=configuration,
        )
        await AgentVersionRepository(db).create(
            agent_id=agent.id,
            version="v1",
            name=name,
            instructions=instructions,
            configuration=configuration,
            status="draft",
            created_by=user_id,
        )
        db.add(AuditLog(
            organization_id=organization_id,
            actor_user_id=user_id,
            action="agent.created",
            resource_type="agent",
            resource_id=agent.id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            metadata={"agent_name": agent.name, "source": "koro"},
        ))
        await db.commit()
        await db.refresh(agent)
        return KoroActionResponse(
            type="create_agent",
            status="ok",
            detail=(
                f"Created the “{agent.name}” agent ({agent_type}) with a draft version. "
                "Open Agents to refine its instructions or run it."
            ),
            data={
                "agent_id": str(agent.id),
                "agent": {
                    "id": str(agent.id),
                    "name": agent.name,
                    "agent_type": agent_type,
                    "instructions": instructions,
                },
            },
        )

    if action_type in ("build", "import_workflow"):
        await require_permission("workflow:create")(request, db)
        definition = _prepare_definition(action.get("definition"))
        if definition is None:
            return KoroActionResponse(
                type=action_type, status="failed",
                detail=("I need a complete workflow definition to build that. "
                        "Describe the trigger and the steps you want."),
            )
        verdict = validate_definition(definition)
        if not verdict.valid:
            return KoroActionResponse(
                type=action_type, status="failed", detail=_definition_errors(verdict),
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
            type=action_type,
            status="ok",
            detail=(
                f"Built “{workflow.name}” with {len(definition['nodes'])} node(s) and "
                f"{len(definition['edges'])} connection(s) as a draft. Open it and run it to test."
            ),
            workflow_id=str(workflow.id),
            data={"definition": definition},
        )

    workflow = _resolve_workflow(workflows, action.get("workflow"), current_id)
    if workflow is None:
        return KoroActionResponse(
            type=action_type,
            status="failed",
            detail="I couldn't match that workflow. Try its exact name.",
        )

    if action_type == "test_node":
        await require_permission("workflow:execute")(request, db)
        latest = await _latest_version(db, workflow.id)
        if latest is None:
            return KoroActionResponse(
                type="test_node", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no saved version to test.",
            )
        definition = latest.definition or {}
        reference = str(action.get("node") or action.get("node_id") or "").strip()
        node = _find_endpoint(definition, reference) if reference else None
        if node is None:
            return KoroActionResponse(
                type="test_node", status="failed", workflow_id=str(workflow.id),
                detail=(
                    f"I couldn't find the step {reference!r} in “{workflow.name}”. "
                    "Ask me to explain the workflow to see its steps."
                ),
            )
        from openagent.runtime.local_engine import run_single_node

        sample = action.get("input") if isinstance(action.get("input"), dict) else {}
        try:
            result = await run_single_node(
                definition,
                str(node.get("id")),
                sample,
                db=db,
                organization_id=organization_id,
            )
        except ValueError as exc:
            return KoroActionResponse(
                type="test_node", status="failed", workflow_id=str(workflow.id),
                detail=str(exc),
            )
        state = str(result.get("status") or "unknown")
        rendered = json.dumps(result.get("outputs") or {}, default=str)
        node_name = str(node.get("name") or node.get("id"))
        detail = f"Tested “{node_name}” on “{workflow.name}”: {state}."
        if result.get("error"):
            detail += f" Error: {result['error']}"
        else:
            detail += f" Output: {rendered[:300]}{'…' if len(rendered) > 300 else ''}"
        data: Dict[str, Any] = {
            "node_id": str(node.get("id")),
            "node_name": node_name,
            "status": state,
            "items": len(result.get("items") or []),
        }
        if len(rendered) <= 4000:
            data["outputs"] = result.get("outputs") or {}
        return KoroActionResponse(
            type="test_node",
            status="ok" if state in ("succeeded", "waiting", "skipped") else "failed",
            workflow_id=str(workflow.id),
            detail=detail,
            data=data,
        )

    if action_type == "update_workflow":
        await require_permission("workflow:update")(request, db)
        new_name = action.get("name")
        description = action.get("description")
        changed: List[str] = []
        if isinstance(new_name, str) and new_name.strip():
            cleaned = _clean_name(new_name, workflow.name)
            if cleaned and cleaned != workflow.name:
                base_slug = slugify(cleaned) or workflow.slug
                slug = base_slug
                suffix = 2
                while any(item.id != workflow.id and item.slug == slug for item in workflows):
                    slug = f"{base_slug}-{suffix}"
                    suffix += 1
                workflow.name = cleaned[:255]
                workflow.slug = slug
                changed.append(f"name “{workflow.name}”")
        if isinstance(description, str) and description.strip():
            workflow.description = description.strip()[:2000]
            changed.append("description")
        if not changed:
            return KoroActionResponse(
                type="update_workflow", status="failed", workflow_id=str(workflow.id),
                detail="Give me the new name or description for this workflow.",
            )
        await _record_audit(
            db, request, organization_id, user_id, "workflow.updated", workflow,
            {"fields": changed, "source": "koro"},
        )
        await db.commit()
        await db.refresh(workflow)
        return KoroActionResponse(
            type="update_workflow", status="ok", workflow_id=str(workflow.id),
            detail=f"Updated “{workflow.name}”: " + ", ".join(changed) + ".",
            data={
                "name": workflow.name,
                "slug": workflow.slug,
                "description": workflow.description,
            },
        )

    if action_type == "delete":
        await require_permission("workflow:delete")(request, db)
        await _record_audit(
            db, request, organization_id, user_id, "workflow.deleted", workflow,
            {"source": "koro"},
        )
        await WorkflowRepository(db).soft_delete_with_org(workflow.id, organization_id)
        await db.commit()
        return KoroActionResponse(
            type="delete", status="ok", workflow_id=str(workflow.id),
            detail=f"Deleted “{workflow.name}”. Its run history is kept for audit.",
        )

    if action_type == "duplicate":
        await require_permission("workflow:create")(request, db)
        latest = await _latest_version(db, workflow.id)
        base_slug = f"{workflow.slug}-copy"
        slug = base_slug
        suffix = 2
        while any(item.slug == slug for item in workflows):
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        copy = await WorkflowRepository(db).create(
            organization_id=organization_id,
            name=f"{workflow.name} (copy)"[:255],
            slug=slug,
            description=workflow.description,
            status=WorkflowStatus.DRAFT,
            metadata=dict(workflow.metadata or {}),
        )
        definition = dict(latest.definition) if latest else starter_definition()
        await _create_version(db, copy, definition, user_id)
        await _record_audit(
            db, request, organization_id, user_id, "workflow.created", copy,
            {"duplicated_from": str(workflow.id), "source": "koro"},
        )
        await db.commit()
        await db.refresh(copy)
        return KoroActionResponse(
            type="duplicate", status="ok", workflow_id=str(copy.id),
            detail=f"Duplicated “{workflow.name}” as “{copy.name}” (draft).",
            data={"source_workflow_id": str(workflow.id)},
        )

    if action_type == "versions":
        await require_permission("workflow:read")(request, db)
        rows = await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(20)
        )
        versions = list(rows.scalars().all())
        if not versions:
            return KoroActionResponse(
                type="versions", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no saved versions yet.",
            )
        detail = (
            f"“{workflow.name}” versions (newest first): "
            + ", ".join(f"{item.version} ({item.status})" for item in versions[:10])
        )
        return KoroActionResponse(
            type="versions", status="ok", workflow_id=str(workflow.id), detail=detail,
            data={"versions": [
                {
                    "version": item.version,
                    "status": item.status,
                    "schema_version": item.schema_version,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in versions
            ]},
        )

    if action_type == "restore":
        await require_permission("workflow:update")(request, db)
        reference = str(action.get("version") or "").strip()
        if not reference:
            return KoroActionResponse(
                type="restore", status="failed", workflow_id=str(workflow.id),
                detail="Which version should I restore? Ask for versions to see the list.",
            )
        snapshot = await WorkflowVersionRepository(db).get_by_workflow_and_version(
            workflow.id, reference,
        )
        if snapshot is None:
            return KoroActionResponse(
                type="restore", status="failed", workflow_id=str(workflow.id),
                detail=f"I couldn't find version {reference!r} of “{workflow.name}”.",
            )
        version = await _create_version(
            db, workflow, dict(snapshot.definition or {}), user_id,
        )
        await _record_audit(
            db, request, organization_id, user_id, "workflow.restored", workflow,
            {"version": version.version, "restored_from": snapshot.version, "source": "koro"},
        )
        await db.commit()
        return KoroActionResponse(
            type="restore", status="ok", workflow_id=str(workflow.id),
            detail=(
                f"Restored {snapshot.version} of “{workflow.name}” as "
                f"{version.version} (draft). Review and publish when ready."
            ),
            data={"restored_from": snapshot.version, "version": version.version},
        )

    if action_type == "runs":
        await require_permission("execution:read")(request, db)
        limit = min(max(int(action.get("limit") or 6), 1), 20)
        rows = await db.execute(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.workflow_id == workflow.id,
                WorkflowExecution.organization_id == organization_id,
            )
            .order_by(WorkflowExecution.created_at.desc())
            .limit(limit)
        )
        executions = list(rows.scalars().all())
        if not executions:
            return KoroActionResponse(
                type="runs", status="ok", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has not been run yet.",
            )
        detail = (
            f"Last {len(executions)} run(s) of “{workflow.name}”: "
            + "; ".join(
                f"{str(getattr(item.status, 'value', item.status))}"
                f" at {item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else 'unknown'}"
                for item in executions
            )
        )
        return KoroActionResponse(
            type="runs", status="ok", workflow_id=str(workflow.id), detail=detail,
            data={"runs": [
                {
                    "id": str(item.id),
                    "workflow_id": str(workflow.id),
                    "workflow_name": workflow.name,
                    "status": str(getattr(item.status, "value", item.status)),
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "error": item.error_message,
                }
                for item in executions
            ]},
        )

    if action_type == "retry":
        await require_permission("workflow:execute")(request, db)
        execution: Optional[WorkflowExecution] = None
        execution_ref = str(action.get("execution_id") or "").strip()
        if execution_ref:
            try:
                execution = await db.get(WorkflowExecution, UUID(execution_ref))
            except (ValueError, TypeError):
                execution = None
            if execution is not None and (
                execution.organization_id != organization_id
                or execution.workflow_id != workflow.id
            ):
                execution = None
        if execution is None:
            rows = await db.execute(
                select(WorkflowExecution)
                .where(
                    WorkflowExecution.workflow_id == workflow.id,
                    WorkflowExecution.status == WorkflowExecutionStatus.FAILED,
                )
                .order_by(WorkflowExecution.created_at.desc())
                .limit(1)
            )
            execution = rows.scalar_one_or_none()
        if execution is None:
            return KoroActionResponse(
                type="retry", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no failed run to retry.",
            )
        latest = await _latest_version(db, workflow.id)
        if latest is None:
            return KoroActionResponse(
                type="retry", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no versions to run.",
            )
        from openagent.runtime.local_engine import run_workflow

        retry_input = action.get("input")
        if not isinstance(retry_input, dict):
            retry_input = (execution.metadata or {}).get("trigger_input")
        result = await run_workflow(
            db,
            workflow=workflow,
            organization_id=organization_id,
            triggered_by=user_id,
            definition=latest.definition or {},
            trigger_input=retry_input if isinstance(retry_input, dict) else {},
            version_label=f"koro retry of {latest.version}",
        )
        return KoroActionResponse(
            type="retry", status="ok" if result.get("status") in ("completed", "waiting") else "failed",
            workflow_id=str(workflow.id),
            detail=(
                f"Retried “{workflow.name}” — {_summary_text(result)}."
            ),
            data={
                "execution_id": result.get("execution_id"),
                "retried_execution_id": str(execution.id),
                "summary": result.get("summary"),
            },
        )

    if action_type == "cancel":
        await require_permission("workflow:execute")(request, db)
        execution = None
        execution_ref = str(action.get("execution_id") or "").strip()
        if execution_ref:
            try:
                candidate = await db.get(WorkflowExecution, UUID(execution_ref))
            except (ValueError, TypeError):
                candidate = None
            if candidate is not None and (
                candidate.organization_id == organization_id
                and candidate.workflow_id == workflow.id
            ):
                execution = candidate
        if execution is None:
            rows = await db.execute(
                select(WorkflowExecution)
                .where(
                    WorkflowExecution.workflow_id == workflow.id,
                    WorkflowExecution.status.in_([
                        WorkflowExecutionStatus.WAITING,
                        WorkflowExecutionStatus.RUNNING,
                        WorkflowExecutionStatus.QUEUED,
                    ]),
                )
                .order_by(WorkflowExecution.created_at.desc())
                .limit(1)
            )
            execution = rows.scalar_one_or_none()
        if execution is None:
            return KoroActionResponse(
                type="cancel", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no waiting or running execution to cancel.",
            )
        from openagent.runtime.workflow_ops import cancel_execution_tree

        cancelled = await cancel_execution_tree(
            db, execution, reason="cancelled through chat"
        )
        await db.commit()
        return KoroActionResponse(
            type="cancel", status="ok", workflow_id=str(workflow.id),
            detail=(
                f"Cancelled the waiting run of “{workflow.name}”."
                if len(cancelled) <= 1
                else f"Cancelled the run of “{workflow.name}” and {len(cancelled) - 1} sub-workflow run(s)."
            ),
            data={"execution_id": str(execution.id), "cancelled": len(cancelled)},
        )

    if action_type == "set_static_data":
        await require_permission("workflow:update")(request, db)
        target_workflow = _resolve_workflow(
            workflows, action.get("workflow"), current_id
        )
        if target_workflow is None:
            return KoroActionResponse(
                type="set_static_data", status="failed",
                detail="Tell me which workflow's static data to change.",
            )
        values = action.get("values")
        if not isinstance(values, dict) or not values:
            return KoroActionResponse(
                type="set_static_data", status="failed",
                workflow_id=str(target_workflow.id),
                detail="Give me the values to store, for example {\"last_seen_id\": 42}.",
            )
        scope = str(action.get("scope") or "global").strip() or "global"
        static = dict(target_workflow.static_data or {})
        bucket = static.get(scope)
        if not isinstance(bucket, dict):
            bucket = {}
        bucket.update(values)
        for key in action.get("delete") or []:
            bucket.pop(str(key), None)
        static[scope] = bucket
        target_workflow.static_data = static
        await db.commit()
        return KoroActionResponse(
            type="set_static_data", status="ok", workflow_id=str(target_workflow.id),
            detail=(
                f"Saved {len(values)} value(s) to the “{scope}” static data "
                f"of “{target_workflow.name}”."
            ),
            data={"scope": scope, "keys": sorted(str(k) for k in bucket)},
        )

    if action_type == "schedule":
        await require_permission("workflow:read")(request, db)
        latest = await _latest_version(db, workflow.id)
        latest_definition = (latest.definition if latest else None) or {}
        from openagent.runtime.schedule_runner import workflow_schedule_summary

        rows = workflow_schedule_summary(workflow.id, latest_definition)
        if not rows:
            return KoroActionResponse(
                type="schedule", status="ok", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no schedule trigger.",
            )
        detail = "; ".join(
            f"{row.get('cron')} ({row.get('timezone')})"
            + (f", next {row['next_runs'][0]}" if row.get("next_runs") else "")
            for row in rows
        )
        return KoroActionResponse(
            type="schedule", status="ok", workflow_id=str(workflow.id),
            detail=f"“{workflow.name}” schedule: {detail}.",
            data={"schedules": rows},
        )

    if action_type == "variables":
        await require_permission("workflow:read")(request, db)
        latest = await _latest_version(db, workflow.id)
        declared = [
            item for item in ((latest.definition if latest else None) or {}).get("variables") or []
            if isinstance(item, dict)
        ]
        detail = (
            f"“{workflow.name}” declares: "
            + ", ".join(
                f"{item.get('name')} ({item.get('type') or 'string'})" for item in declared
            )
            if declared else f"“{workflow.name}” declares no variables."
        )
        return KoroActionResponse(
            type="variables", status="ok", workflow_id=str(workflow.id),
            detail=detail, data={"variables": declared},
        )

    if action_type == "set_variable":
        await require_permission("workflow:update")(request, db)
        latest = await _latest_version(db, workflow.id)
        if latest is None:
            return KoroActionResponse(
                type="set_variable", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no saved version to change.",
            )
        updated, error = _mutate_definition(dict(latest.definition or {}), action)
        if updated is None:
            return KoroActionResponse(
                type="set_variable", status="failed", workflow_id=str(workflow.id),
                detail=error or "I couldn't update that variable.",
            )
        verdict = validate_definition(updated)
        if not verdict.valid:
            return KoroActionResponse(
                type="set_variable", status="failed", workflow_id=str(workflow.id),
                detail=_definition_errors(verdict),
            )
        version = await _create_version(db, workflow, updated, user_id)
        await _record_audit(
            db, request, organization_id, user_id, "workflow.version_created", workflow,
            {"version": version.version, "source": "koro-variable"},
        )
        await db.commit()
        names = ", ".join(
            str(item.get("name")) for item in updated.get("variables") or []
            if isinstance(item, dict)
        )
        return KoroActionResponse(
            type="set_variable", status="ok", workflow_id=str(workflow.id),
            detail=f"Updated variables on “{workflow.name}”: {names}.",
            data={"definition": updated, "version": version.version},
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
            data={"definition": definition, "version": version.version},
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

    if action_type == "export_workflow":
        await require_permission("workflow:read")(request, db)
        latest = await _latest_version(db, workflow.id)
        if latest is None:
            return KoroActionResponse(
                type="export_workflow", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no saved version to export.",
            )
        return KoroActionResponse(
            type="export_workflow", status="ok", workflow_id=str(workflow.id),
            detail=f"Exported “{workflow.name}” ({latest.version}).",
            data={
                "name": workflow.name,
                "description": workflow.description,
                "tags": (workflow.metadata or {}).get("tags") or [],
                "version": latest.version,
                "definition": latest.definition or {},
            },
        )

    if action_type == "node_logs":
        await require_permission("execution:read")(request, db)
        query = select(WorkflowExecution).where(
            WorkflowExecution.workflow_id == workflow.id,
            WorkflowExecution.organization_id == organization_id,
        )
        execution_ref = str(action.get("execution_id") or "").strip()
        if execution_ref:
            try:
                query = query.where(WorkflowExecution.id == UUID(execution_ref))
            except (TypeError, ValueError):
                query = query.where(WorkflowExecution.id == UUID(int=0))
        execution = (
            await db.execute(
                query.order_by(WorkflowExecution.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if execution is None:
            return KoroActionResponse(
                type="node_logs", status="failed", workflow_id=str(workflow.id),
                detail="I couldn't find that run.",
            )
        runs = (execution.metadata or {}).get("node_runs") or []
        reference = str(action.get("node") or "").strip().lower()
        logs: List[Dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            if reference and reference not in (
                str(run.get("node_id") or "").lower(),
                str(run.get("node_name") or "").lower(),
            ):
                continue
            logs.append({
                "node_id": run.get("node_id"),
                "node_name": run.get("node_name"),
                "status": run.get("status"),
                "duration_ms": run.get("duration_ms"),
                "attempt": run.get("attempt"),
                "error": run.get("error"),
                "error_code": run.get("error_code"),
                "items": (run.get("items") or [])[:5],
            })
        if not logs:
            detail = (
                f"No node called {action.get('node')!r} ran in that execution."
                if reference
                else "That execution has no node logs."
            )
            return KoroActionResponse(
                type="node_logs", status="failed",
                workflow_id=str(workflow.id), detail=detail,
            )
        detail = (
            f"{len(logs)} node log(s) from the last run of “{workflow.name}”: "
            + "; ".join(f"{item['node_name']}: {item['status']}" for item in logs[:8])
        )
        return KoroActionResponse(
            type="node_logs", status="ok", workflow_id=str(workflow.id), detail=detail,
            data={"execution_id": str(execution.id), "nodes": logs},
        )

    if action_type == "resume_wait":
        await require_permission("workflow:execute")(request, db)
        execution = (
            await db.execute(
                select(WorkflowExecution)
                .where(
                    WorkflowExecution.workflow_id == workflow.id,
                    WorkflowExecution.organization_id == organization_id,
                    WorkflowExecution.status == WorkflowExecutionStatus.WAITING,
                )
                .order_by(WorkflowExecution.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if execution is None:
            return KoroActionResponse(
                type="resume_wait", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no execution waiting to resume.",
            )
        wait = (execution.metadata or {}).get("wait") or {}
        from openagent.runtime.local_engine import run_workflow

        result = await run_workflow(
            db,
            workflow=workflow,
            organization_id=organization_id,
            triggered_by=user_id,
            definition=wait.get("definition"),
            resume_from=execution,
        )
        return KoroActionResponse(
            type="resume_wait",
            status="ok" if result.get("status") in ("completed", "waiting") else "failed",
            workflow_id=str(workflow.id),
            detail=f"Resumed “{workflow.name}” — {_summary_text(result)}.",
            data={"execution_id": result.get("execution_id"), "summary": result.get("summary")},
        )

    if action_type == "undo_last":
        await require_permission("workflow:update")(request, db)
        rows = await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(2)
        )
        versions = list(rows.scalars().all())
        if len(versions) < 2:
            return KoroActionResponse(
                type="undo_last", status="failed", workflow_id=str(workflow.id),
                detail=f"“{workflow.name}” has no earlier version to undo to.",
            )
        previous = versions[1]
        version = await _create_version(
            db, workflow, dict(previous.definition or {}), user_id,
        )
        await _record_audit(
            db, request, organization_id, user_id, "workflow.undo", workflow,
            {"restored_from": previous.version, "version": version.version, "source": "koro"},
        )
        await db.commit()
        return KoroActionResponse(
            type="undo_last", status="ok", workflow_id=str(workflow.id),
            detail=(
                f"Undid the last change to “{workflow.name}”: restored "
                f"{previous.version} as {version.version} (draft)."
            ),
            data={"restored_from": previous.version, "version": version.version},
        )

    return KoroActionResponse(type=action_type, status="failed", detail="Unsupported action")


def _looks_like_build_request(message: str) -> bool:
    """True for requests that describe an automation, with or without a verb."""
    text = (message or "").lower()
    if re.search(r"\bagent\b", text) and not re.search(r"\bworkflow|automation\b", text):
        return False
    return bool(
        re.search(r"\b(build|generate|design|create|make|automate|workflow)\b", text)
        or re.search(
            r"\b(when|whenever|every|daily|weekly|hourly|each|after|on new|"
            r"as soon as|once)\b",
            text,
        )
    )


def _template_name(message: str) -> str:
    """A readable workflow name from a natural-language request."""
    text = re.sub(r"\s+", " ", message or "").strip()
    cleaned = re.sub(
        r"^(please\s+)?(build|create|generate|make|design|add)\s+(me\s+)?(a|an|the)?\s*",
        "", text, flags=re.I,
    )
    cleaned = re.sub(r"^(workflow|automation|agent)\s+(for|to|that)\s+", "", cleaned, flags=re.I)
    cleaned = cleaned[:70].strip(" .,:;")
    if not cleaned:
        return "Koro Automation"
    return cleaned[0].upper() + cleaned[1:]


def _template_trigger(message: str, name: str) -> Dict[str, Any]:
    text = message.lower()
    if re.search(r"\b(daily|every day|each morning|every morning|morning|nightly|weekly|hourly|schedule|cron|every \d+ (?:minutes?|hours?))\b", text):
        cron = "0 9 * * *"
        minutes = re.search(r"every (\d+) minutes?", text)
        hours = re.search(r"every (\d+) hours?", text)
        if minutes:
            cron = f"*/{max(1, min(int(minutes.group(1)), 59))} * * * *"
        elif hours:
            cron = f"0 */{max(1, min(int(hours.group(1)), 23))} * * *"
        elif "hourly" in text:
            cron = "0 * * * *"
        elif "weekly" in text:
            cron = "0 9 * * 1"
        elif "nightly" in text or "evening" in text:
            cron = "0 18 * * *"
        return {"id": "trigger", "type": "schedule", "name": "On schedule",
                "config": {"cron": cron}, "position": {"x": 80, "y": 220}}
    if re.search(r"\b(webhook|http request|incoming request|api call)\b", text):
        return {"id": "trigger", "type": "webhook", "name": "Webhook",
                "config": {"path": f"/hooks/{slugify(name) or 'koro'}", "method": "POST"},
                "position": {"x": 80, "y": 220}}
    if re.search(r"\b(when|whenever|on new|new email|new message|new lead|new ticket|new invoice|received|event)\b", text):
        event = "event.received"
        for marker, value in (
            ("email", "email.received"),
            ("invoice", "invoice.received"),
            ("lead", "lead.created"),
            ("ticket", "ticket.created"),
            ("message", "message.received"),
            ("order", "order.created"),
        ):
            if marker in text:
                event = value
                break
        return {"id": "trigger", "type": "event", "name": "On event",
                "config": {"event": event}, "position": {"x": 80, "y": 220}}
    return {"id": "trigger", "type": "manual", "name": "Manual start",
            "config": {}, "position": {"x": 80, "y": 220}}


def _template_definition(message: str, model: str) -> Dict[str, Any]:
    """Build a valid workflow from keywords when no model is available.

    This is deliberately conservative: a linear chain of real nodes that the
    user can edit, rather than pretending to understand every nuance. It keeps
    the assistant useful on a laptop with no model installed at all.
    """
    text = message.lower()
    name = _template_name(message)
    trigger = _template_trigger(message, name)
    default_model = model or "qwen2.5:0.5b-instruct"
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    def add_node(
        node_type: str,
        node_name: str,
        config: Dict[str, Any],
        *,
        y: int = 220,
        notes: str | None = None,
    ) -> str:
        node_id = f"n{len(nodes) + 1}"
        entry: Dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "name": node_name,
            "config": config,
            "position": {"x": 380 + len(nodes) * 280, "y": y},
        }
        if notes:
            entry["notes"] = notes
        nodes.append(entry)
        return node_id

    def connect(source: str, target: str, label: str | None = None) -> None:
        edge: Dict[str, Any] = {"id": f"e{len(edges) + 1}", "from": source, "to": target}
        if label:
            edge["label"] = label
        edges.append(edge)

    wants_ai = bool(re.search(
        r"\b(?:ai|summar|extract|classif|analy[sz]|draft|generat|writ|"
        r"compar|check|triag|understand|pars|decid)",
        text,
    ))
    wants_condition = bool(re.search(
        r"\b(?:if|discrepanc|mismatch|match|validat|greater|less|equals?|"
        r"over|under|exceed)",
        text,
    ))
    wants_notify = bool(re.search(
        r"\b(?:notif|alert|slack|teams|discord|ping|send|sent|webhook|messag)",
        text,
    ))
    wants_log = bool(re.search(
        r"\b(?:sheet|spreadsheet|log|record|sav|stor|database|notion|airtable|row)",
        text,
    ))

    previous = trigger["id"]

    normalize = add_node(
        "set",
        "Normalize input",
        {"values": {"request": message[:400], "received_at": "{{trigger.received_at}}"}},
    )
    connect(previous, normalize)
    previous = normalize

    if wants_ai:
        prompt = add_node(
            "prompt",
            "Understand the request",
            {
                "model": default_model,
                "system": (
                    "You are a precise automation assistant. Read the input and return "
                    "the structured result the workflow needs."
                ),
                "prompt": message[:1500],
            },
            notes="Uses the local model configured for the copilot.",
        )
        connect(previous, prompt)
        previous = prompt

    if wants_condition:
        condition = add_node(
            "condition",
            "Check the result",
            {"expression": f"{{{{nodes.{previous}.output}}}} != null"},
        )
        connect(previous, condition)
        yes = add_node(
            "set", "Handle match",
            {"values": {"result": "matched", "source": "{{trigger.id}}"}},
            y=80,
        )
        no = add_node(
            "set", "No action needed",
            {"values": {"result": "no_match"}},
            y=360,
        )
        connect(condition, yes, "true")
        connect(condition, no, "false")
        if wants_notify:
            alert = add_node(
                "webhook", "Send alert",
                {
                    "url": "https://hooks.slack.com/services/REPLACE/ME/TOKEN",
                    "method": "POST",
                    "body": {"text": "A workflow result needs attention: {{trigger.id}}"},
                },
                y=80,
                notes="Replace the URL with your Slack, Teams or Discord webhook.",
            )
            connect(yes, alert)
        previous = None
    elif wants_notify:
        alert = add_node(
            "webhook", "Send notification",
            {
                "url": "https://hooks.slack.com/services/REPLACE/ME/TOKEN",
                "method": "POST",
                "body": {"text": "Workflow update: {{trigger.id}}"},
            },
            notes="Replace the URL with your Slack, Teams or Discord webhook.",
        )
        connect(previous, alert)
        previous = alert

    if wants_log and previous is not None:
        record = add_node(
            "set", "Record result",
            {"values": {"status": "completed", "result": "{{nodes.%s.output}}" % previous}},
        )
        connect(previous, record)
        previous = record

    if previous is not None:
        finish = add_node(
            "set", "Finish",
            {"values": {"status": "completed"}},
        )
        connect(previous, finish)

    return {
        "schema_version": "1.0",
        "triggers": [trigger],
        "nodes": nodes,
        "edges": edges,
        "variables": [],
        "settings": {},
    }


def _append_template_node(
    definition: Dict[str, Any], message: str, model: str,
) -> Optional[Dict[str, Any]]:
    """Append one sensible node to an existing graph for offline edit commands."""
    if not isinstance(definition, dict):
        return None
    text = message.lower()
    nodes = [dict(node) for node in definition.get("nodes") or [] if isinstance(node, dict)]
    edges = [dict(edge) for edge in definition.get("edges") or [] if isinstance(edge, dict)]
    outgoing = {str(edge.get("from")) for edge in edges}
    terminals = [node for node in nodes if str(node.get("id")) not in outgoing]
    if not terminals:
        return None
    anchor = str(terminals[0].get("id"))
    default_model = model or "qwen2.5:0.5b-instruct"

    node_type = ""
    node_name = ""
    config: Dict[str, Any] = {}
    notes: Optional[str] = None
    if re.search(r"\b(?:slack|teams|discord|notif|alert|ping|send|webhook|messag)", text):
        node_type, node_name = "webhook", "Send notification"
        config = {
            "url": "https://hooks.slack.com/services/REPLACE/ME/TOKEN",
            "method": "POST",
            "body": {"text": message[:300]},
        }
        notes = "Replace the URL with your chat or webhook endpoint."
    elif re.search(r"\b(?:ai|summar|extract|classif|analy[sz]|draft|writ)", text):
        node_type, node_name = "prompt", "AI step"
        config = {"model": default_model, "prompt": message[:1500]}
    elif re.search(r"\b(?:sheet|spreadsheet|log|record|sav|stor|database)", text):
        node_type, node_name = "set", "Record result"
        config = {"values": {"result": "{{nodes.%s.output}}" % anchor}}
    elif re.search(r"\b(delay|wait|pause)\b", text):
        node_type, node_name = "delay", "Wait"
        config = {"duration_seconds": 60}
    else:
        return None

    existing_ids = {str(node.get("id")) for node in nodes} | {
        str(trigger.get("id")) for trigger in definition.get("triggers") or [] if isinstance(trigger, dict)
    }
    index = len(nodes) + 1
    while f"n{index}" in existing_ids:
        index += 1
    new_id = f"n{index}"
    new_node: Dict[str, Any] = {
        "id": new_id,
        "type": node_type,
        "name": node_name,
        "config": config,
        "position": {"x": 380 + len(nodes) * 280, "y": 220},
    }
    if notes:
        new_node["notes"] = notes
    edges.append({"id": f"e{len(edges) + 1}", "from": anchor, "to": new_id})
    return {
        **definition,
        "nodes": [*nodes, new_node],
        "edges": edges,
    }


def _seconds_from_text(amount: str, unit: str) -> int:
    """A quantity + unit phrase as seconds, bounded to one execution budget."""
    try:
        value = int(amount)
    except (TypeError, ValueError):
        value = 0
    unit = (unit or "").lower()
    if unit.startswith("h"):
        seconds = value * 3600
    elif unit.startswith("m"):
        seconds = value * 60
    else:
        seconds = value
    return max(1, min(seconds, 86400))


def _delay_seconds_from_text(text: str) -> int:
    """Read \"wait 5 minutes\" when adding a delay step; default stays 60s."""
    match = re.search(
        r"\b(\d{1,5})\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|h)\b",
        text,
    )
    if not match:
        return 60
    return _seconds_from_text(match.group(1), match.group(2))


def _cron_from_text(text: str) -> Optional[str]:
    """Best-effort cron for "every morning at 9" style requests."""
    lowered = (text or "").lower()
    minutes = re.search(r"every\s+(\d+)\s+minutes?", lowered)
    if minutes:
        return f"*/{max(1, min(int(minutes.group(1)), 59))} * * * *"
    hours = re.search(r"every\s+(\d+)\s+hours?", lowered)
    if hours:
        return f"0 */{max(1, min(int(hours.group(1)), 23))} * * *"
    if "hourly" in lowered:
        return "0 * * * *"

    hour: Optional[int] = None
    minute = 0
    at = re.search(r"\b(?:at|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if at:
        hour = int(at.group(1))
        minute = int(at.group(2) or 0)
        meridiem = at.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if not 0 <= hour <= 23:
            hour = None
        minute = min(max(minute, 0), 59)

    if "weekly" in lowered or "every week" in lowered:
        return f"{minute} {hour if hour is not None else 9} * * 1"
    if "nightly" in lowered or "evening" in lowered:
        return f"{minute} {hour if hour is not None else 18} * * *"
    if hour is not None:
        return f"{minute} {hour} * * *"
    if any(word in lowered for word in ("daily", "every day", "every morning", "morning")):
        return f"{minute} 9 * * *"
    return None


_CONFIRM_RE = re.compile(r"^\s*confirm\s+([A-Za-z0-9._\-]+)\s*$", re.I)


def _confirmation_plan(message: str) -> Optional[Dict[str, Any]]:
    """Turn “confirm <token>” into the action the token was issued for."""
    match = _CONFIRM_RE.match(message or "")
    if match is None:
        return None
    token = match.group(1)
    decoded = decode_confirmation(token)
    if decoded is None:
        return None
    return {"reply": "", "action": {**decoded, "confirm": token}}


def _command_plan(message: str, workflows: List[Workflow], current_id: Optional[UUID]) -> Optional[Dict[str, Any]]:
    """Deterministic offline intent parser — Koro keeps working without a model."""
    text = message.lower().strip()

    def target(exclude: Optional[str] = None) -> Optional[str]:
        for workflow in workflows:
            if exclude and workflow.name.lower() == exclude.lower():
                continue
            if workflow.name.lower() in text or workflow.slug.lower() in text:
                return workflow.name
        if current_id:
            return str(current_id)
        return workflows[0].name if len(workflows) == 1 else None

    if re.fullmatch(
        r"(?:please\s+)?(?:undo|revert)(?:\s+(?:that|last|the last change))?[.!]?", text
    ):
        return {"reply": "", "action": {"type": "undo_last"}}

    if re.search(r"\bwhat can you do\b|\bhelp\b|^koro\?*$", text):
        return {
            "reply": (
                "I can do the whole job from here: build or edit a workflow, add, "
                "update, connect or remove nodes, set variables and schedules, "
                "run/test/retry/cancel, publish or unpublish, list versions and "
                "restore one, inspect runs, create and list agents, and list "
                "credentials (names only). Describe what you want."
            ),
            "action": None,
        }
    if (
        re.search(
            r"\b(?:create|make|build|design)\s+(?:me\s+)?(?:a|an|the)?\s*(?:new\s+)?agent\b",
            text,
        )
        or re.search(r"\bnew\s+agent\b", text)
    ):
        quoted = re.search(r"[\"'“”]([^\"'“”]{2,80})[\"'“”]", message)
        named = re.search(r"\b(?:called|named)\s+([\w \-]{2,80})", message, re.I)
        descriptor = re.search(
            r"\bagent\s+(?:that|which|to|for)\s+(.{3,60})", message, re.I
        )
        if quoted:
            name = quoted.group(1)
        elif named:
            name = named.group(1)
        elif descriptor:
            name = _template_name(descriptor.group(1))
        else:
            name = "Koro Agent"
        agent_type = "autonomous" if re.search(r"\b(autonomous|monitor|watch)\b", text) else "chat"
        return {
            "reply": f"Creating the “{name.strip()}” agent.",
            "action": {
                "type": "create_agent",
                "agent": {
                    "name": name.strip(),
                    "description": message.strip()[:300],
                    "agent_type": agent_type,
                    "instructions": message.strip()[:2000],
                },
            },
        }
    if re.search(r"\b(test|try|execute|run)\b", text) and re.search(r"\b(node|step)\b", text):
        quoted = re.search(r"[\"'“”]([^\"'“”]{2,60})[\"'“”]", message)
        named = re.search(
            r"\b(?:node|step)\s+(?:called\s+|named\s+)?([\w][\w \-]{1,59})",
            message, re.I,
        )
        reference = (quoted or named).group(1).strip() if (quoted or named) else None
        if reference:
            # "test the Send notification node in Lead intake" names one step.
            reference = re.split(
                r"\s+(?:from|in|on|of|for|and)\s+", reference, maxsplit=1
            )[0].strip()
            reference = re.sub(r"\s+(?:node|step)$", "", reference, flags=re.I).strip()
        if reference and not re.search(r"\b(workflow|automation|whole)\b", reference, re.I):
            return {
                "reply": f"Testing the “{reference}” step.",
                "action": {"type": "test_node", "workflow": target(), "node": reference},
            }
    if (
        re.search(r"\bsub-?workflow\b", text)
        and re.search(r"\b(link|use|run|call|connect)\b", text)
        and not re.search(r"\bcreate\s+(?:a\s+)?sub", text)
    ):
        found = sorted(
            (
                (workflow.name.lower(), text.find(workflow.name.lower()), workflow)
                for workflow in workflows
                if workflow.name.lower() in text
            ),
            key=lambda entry: entry[1],
        )
        child = parent = None
        if len(found) >= 2:
            first, second = found[0][2], found[1][2]
            if re.search(r"\buse\b.+\bas\b", text):
                child, parent = first, second
            else:
                parent, child = first, second
        elif len(found) == 1:
            child = found[0][2]
        if child is not None:
            node_match = re.search(
                r"\b(?:node|step)\s+(?:called\s+|named\s+)?([\w][\w \-]{1,59})",
                message, re.I,
            )
            return {
                "reply": f"Linking “{child.name}” as a sub-workflow.",
                "action": {
                    "type": "link_subworkflow",
                    "workflow": parent.name if parent is not None else target(),
                    "sub_workflow": child.name,
                    "node": node_match.group(1).strip() if node_match else None,
                },
            }
    if re.search(
        r"\b(retry|on error|error output|execute once|always output|timeout)\b", text
    ) and re.search(r"\bnode\b", text):
        quoted = re.search(r"[\"'“”]([^\"'“”]{2,60})[\"'“”]", message)
        named = re.search(r"\b([\w][\w \-]{1,59}?)\s+node\b", message, re.I)
        reference = (quoted or named).group(1).strip() if (quoted or named) else None
        if reference:
            reference = re.sub(
                r"^(?:please\s+)?(?:retry|test|run|execute|set|change|update|make)\s+(?:the\s+)?",
                "", reference, flags=re.I,
            ).strip()
            reference = re.sub(r"^(?:the|a|an)\s+", "", reference, flags=re.I).strip()
            settings: Dict[str, Any] = {
                "type": "set_node_settings",
                "workflow": target(),
                "node": reference,
            }
            if "on error" in text:
                settings["on_error"] = (
                    "continueErrorOutput" if "error output" in text else "continue"
                )
            if re.search(r"\bretry\b", text):
                settings["retry_on_fail"] = True
                tries = re.search(r"\b(\d+)\s+times?\b", text)
                settings["max_tries"] = (
                    max(1, min(int(tries.group(1)), 10)) if tries else 3
                )
            if "execute once" in text:
                settings["execute_once"] = True
            if "always output" in text:
                settings["always_output_data"] = True
            timeout = re.search(
                r"\b(\d{1,5})\s*(second|seconds|sec|secs|minute|minutes|min|mins)\b",
                text,
            )
            if "timeout" in text and timeout:
                settings["timeout_seconds"] = _seconds_from_text(
                    timeout.group(1), timeout.group(2)
                )
            if len(settings) > 3:
                return {
                    "reply": f"Updating the “{reference}” node settings.",
                    "action": settings,
                }
    if re.search(r"\brename\b", text) or (
        re.search(r"\bcall\b", text)
        and re.search(r"\b(workflow|automation|this|it)\b", text)
    ):
        quoted = re.search(r"[\"'“”]([^\"'“”]{2,80})[\"'“”]", message)
        named = re.search(
            r"\brenam(?:e|ing)\b[^.]*?\b(?:to|as|called)\s+([\w][\w \-]{1,79})",
            message, re.I,
        )
        name = (quoted or named).group(1).strip() if (quoted or named) else None
        if name and not re.search(r"\b(workflow|automation)\b", name, re.I):
            return {
                "reply": f"Renaming it to “{name}”.",
                "action": {"type": "update_workflow", "workflow": target(), "name": name},
            }
    if re.search(r"\bconcurren\w*", text):
        match = re.search(r"\b(\d{1,2})\b", text)
        if match:
            value = int(match.group(1))
            return {
                "reply": f"Limiting it to {value} parallel run(s).",
                "action": {
                    "type": "set_workflow_setting",
                    "workflow": target(),
                    "max_concurrency": value,
                },
            }
        if re.search(r"\b(unlimited|remove|clear|no limit)\b", text):
            return {
                "reply": "Removing the concurrency limit.",
                "action": {
                    "type": "set_workflow_setting",
                    "workflow": target(),
                    "max_concurrency": 0,
                },
            }
    if re.search(r"\b(timeout|time limit|execution budget)\b", text):
        amount = re.search(
            r"\b(\d{1,5})\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|h)\b",
            text,
        )
        if amount:
            seconds = _seconds_from_text(amount.group(1), amount.group(2))
            return {
                "reply": f"Setting the execution budget to {seconds}s.",
                "action": {
                    "type": "set_workflow_setting",
                    "workflow": target(),
                    "execution_timeout_seconds": seconds,
                },
            }
    if re.search(
        r"\b(on error|error workflow)\b|\b(?:when|if)\b[^.]{0,60}?\bfails?\b",
        text,
    ):
        if re.search(r"\b(clear|remove|disable|none|stop)\b", text):
            return {
                "reply": "Clearing the error workflow.",
                "action": {
                    "type": "set_workflow_setting",
                    "workflow": target(),
                    "clear_error_workflow": True,
                },
            }
        match = re.search(
            r"\b(?:run|use|call|then)\s+[\"'“”]?([\w][\w \-]{1,79}?)[\"'“”]?\s*[.!]?$",
            message,
            re.I,
        )
        if match:
            reference = re.split(
                r"\s+(?:in|for|on|of|from)\s+", match.group(1).strip(), maxsplit=1
            )[0].strip()
            return {
                "reply": f"Routing failures to “{reference}”.",
                "action": {
                    "type": "set_workflow_setting",
                    "workflow": target(exclude=reference),
                    "error_workflow": reference,
                },
            }
    if (
        re.search(r"\b(publish|activate|go live)\b", text)
        and re.search(r"\b(run|test|execute|trigger)\b", text)
    ):
        workflow = target()
        return {
            "reply": "Publishing it and running a test.",
            "action": {
                "type": "batch",
                "workflow": workflow,
                "actions": [
                    {"type": "publish", "workflow": workflow},
                    {"type": "run", "workflow": workflow},
                ],
            },
        }
    if re.search(
        r"\b(can you|do you support|is there a node|node coverage|coverage|built[- ]?in)\b",
        text,
    ):
        return {
            "reply": "Checking what is actually built in.",
            "action": {"type": "node_coverage", "query": message},
        }
    if (
        re.search(r"\b(list|show|which|what)\b", text)
        and re.search(r"\bagents?\b", text)
        and not re.search(r"\b(create|make|build|design)\b", text)
    ):
        return {"reply": "Here are your agents.", "action": {"type": "list_agents"}}
    if (
        re.search(r"\b(list|show|which|what)\b", text)
        and re.search(r"\b(credentials?|integrations?)\b", text)
        and not re.search(r"\b(add|create|new|rotate|revoke|delete)\b", text)
    ):
        return {
            "reply": "Here are your credentials (names and providers only).",
            "action": {"type": "list_credentials"},
        }
    if re.search(r"\b(list|show|what)\b", text) and re.search(r"\bvariables?\b", text):
        return {"reply": "Here are the declared variables.", "action": {"type": "variables", "workflow": target()}}
    if re.search(r"\b(declare|add|set|create)\b", text) and re.search(r"\bvariable\b", text):
        quoted = re.search(r"[\"“]([A-Za-z_][A-Za-z0-9_]*)[\"”]", message)
        named = re.search(
            r"\b(?:variable|input)\s+(?:called|named)\s+([A-Za-z_][A-Za-z0-9_]*)",
            message, re.I,
        )
        bare = re.search(r"\bvariable\s+([A-Za-z_][A-Za-z0-9_]*)", message, re.I)
        name = (quoted or named or bare).group(1) if (quoted or named or bare) else "input"
        if re.search(r"\b(number|integer|count|amount|price)\b", text):
            value_type = "number"
        elif re.search(r"\b(boolean|true|false|flag)\b", text):
            value_type = "boolean"
        elif re.search(r"\b(json|object|array|list)\b", text):
            value_type = "json"
        else:
            value_type = "string"
        return {
            "reply": f"Declaring the “{name}” variable as {value_type}.",
            "action": {
                "type": "set_variable",
                "workflow": target(),
                "variables": {name: {"type": value_type}},
            },
        }
    if re.search(r"\b(restore|roll ?back|revert)\b", text):
        match = re.search(r"\bv(\d+)\b", text, re.I) or re.search(r"\bversion\s+(\d+)\b", text, re.I)
        return {
            "reply": "Restoring that version as a new draft.",
            "action": {
                "type": "restore",
                "workflow": target(),
                "version": f"v{match.group(1)}" if match else None,
            },
        }
    if re.search(r"\b(versions?|revisions?|version history)\b", text):
        return {"reply": "Here are the saved versions.", "action": {"type": "versions", "workflow": target()}}
    if (
        re.search(r"\b(delete|remove|trash)\b", text)
        and re.search(r"\b(workflow|automation)\b", text)
        and not re.search(r"\bnode\b", text)
    ):
        return {"reply": "Deleting that workflow.", "action": {"type": "delete", "workflow": target()}}
    if (
        re.search(r"\b(duplicate|clone|copy)\b", text)
        and re.search(r"\b(workflow|automation)\b", text)
    ):
        return {"reply": "Duplicating it.", "action": {"type": "duplicate", "workflow": target()}}
    if (
        re.search(r"\b(runs?|executions?)\b", text)
        and re.search(r"\b(show|list|last|latest|recent|history|what)\b", text)
        and not re.search(r"\b(versions?|revisions?)\b", text)
        and not re.search(r"\b(happened|why|fail(?:ed|ure)?|error|wrong|status)\b", text)
    ):
        count = re.search(r"\b(?:last|latest|recent)\s+(\d+)\b", text) or re.search(
            r"\b(\d+)\s+(?:runs?|executions?)\b", text,
        )
        return {
            "reply": "Here are the recent runs.",
            "action": {
                "type": "runs",
                "workflow": target(),
                "limit": int(count.group(1)) if count else 6,
            },
        }
    if re.search(r"\b(retry|re-?run|run\s+(?:it\s+)?again|try\s+again)\b", text):
        return {"reply": "Retrying the last failed run.", "action": {"type": "retry", "workflow": target()}}
    if (
        re.search(r"\b(cancel|stop|abort)\b", text)
        and re.search(r"\b(run|execution|waiting|it)\b", text)
    ):
        return {"reply": "Cancelling the waiting run.", "action": {"type": "cancel", "workflow": target()}}
    if (
        re.search(r"\b(schedule|cron)\b", text)
        and re.search(r"\b(set|change|update|make|every|daily|hourly|weekly|nightly)\b", text)
    ):
        cron = _cron_from_text(text)
        if cron:
            return {
                "reply": f"Setting the schedule to {cron}.",
                "action": {"type": "set_schedule", "workflow": target(), "cron": cron},
            }
    if re.search(r"\b(add|insert|append)\b", text):
        kind = ""
        if re.search(r"\b(slack|teams|discord|notification|notify|webhook|http)\b", text):
            kind = "webhook"
        elif re.search(r"\b(ai|prompt|summari[sz]e|classify|extract|draft)\b", text):
            kind = "prompt"
        elif re.search(r"\b(delay|wait|pause)\b", text):
            kind = "delay"
        elif re.search(r"\b(set|transform|record|log)\b", text):
            kind = "set"
        if kind:
            workflow = target()
            if workflow is None:
                # No workflow is resolvable in this turn; let the canvas-edit
                # fallback decide instead of inventing a workflow name.
                return None
            configs: Dict[str, Dict[str, Any]] = {
                "webhook": {
                    "url": "https://hooks.slack.com/services/REPLACE/ME/TOKEN",
                    "method": "POST",
                    "body": {"text": message[:300]},
                },
                "prompt": {
                    "model": "qwen2.5:0.5b-instruct",
                    "prompt": message[:1500],
                },
                "delay": {"duration_seconds": _delay_seconds_from_text(text)},
                "set": {"values": {"status": "pending"}},
            }
            names = {
                "webhook": "Send notification",
                "prompt": "AI step",
                "delay": "Wait",
                "set": "Set values",
            }
            return {
                "reply": f"Adding a {names[kind].lower()} step to the workflow.",
                "action": {
                    "type": "add_node",
                    "workflow": target(),
                    "node": {"type": kind, "name": names[kind], "config": configs[kind]},
                },
            }
    if re.search(r"\b(remove|delete)\b", text) and re.search(r"\bnode\b", text):
        quoted = re.search(r"[\"“]([^\"”]{2,60})[\"”]", message)
        named = re.search(r"\bnode\s+(?:called|named)\s+([\w \-]{2,60})", message, re.I)
        reference = (quoted or named).group(1) if (quoted or named) else None
        if reference:
            return {
                "reply": "Removing that node.",
                "action": {"type": "remove_node", "workflow": target(), "node": reference.strip()},
            }
    if re.search(r"\b(disconnect|unlink)\b", text):
        match = re.search(
            r"\b(?:disconnect|unlink)\s+(?:the\s+)?(.+?)\s+(?:from)\s+(?:the\s+)?(.+?)(?:\s+(?:node|step))?\s*[.!]?$",
            message, re.I,
        )
        if match:
            return {
                "reply": "Removing that connection.",
                "action": {
                    "type": "disconnect",
                    "workflow": target(),
                    "from": match.group(1).strip(" “”\"'"),
                    "to": match.group(2).strip(" “”\"'"),
                },
            }
    if re.search(r"\b(connect|link)\b", text):
        match = re.search(
            r"\b(?:connect|link)\s+(?:the\s+)?(.+?)\s+(?:to|with)\s+(?:the\s+)?(.+?)(?:\s+(?:node|step))?\s*[.!]?$",
            message, re.I,
        )
        if match:
            return {
                "reply": "Connecting those nodes.",
                "action": {
                    "type": "connect",
                    "workflow": target(),
                    "from": match.group(1).strip(" “”\"'"),
                    "to": match.group(2).strip(" “”\"'"),
                },
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
    # Inspect before run: "last run" contains the word "run". Keep these
    # patterns specific so ordinary edit requests ("log the result to a
    # spreadsheet") are not mistaken for a status check.
    if re.search(
        r"\b(last run|last execution|what happened|what went wrong|"
        r"(?:show|check|get)\s+(?:me\s+)?(?:the\s+)?(?:status|results?|logs?|execution)|"
        r"status\s+of|results?\s+of|logs?\s+of|execution\s+status)\b",
        text,
    ):
        return {"reply": "Checking the latest run.", "action": {"type": "inspect", "workflow": target()}}
    if re.search(
        r"^\s*(?:run|test|execute|start|trigger)\b"
        r"|\b(?:run|test|execute|start|trigger)\s+(?:it|this|that|"
        r"the\s+(?:workflow|automation))\b",
        text,
    ):
        return {"reply": "Running it.", "action": {"type": "run", "workflow": target()}}
    if re.search(r"\b(create|new|make|add)\b", text) and re.search(r"\bworkflow|automation\b", text):
        # Descriptive requests ("create a workflow that …") go to the generator
        # below; only bare "create a workflow" gets the starter definition.
        if re.search(
            r"\b(that|which|when|whenever|after|before|if|every|for|to|with|from|so)\b",
            text,
        ):
            return None
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


def _conversation_title(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message or "").strip()
    if not cleaned:
        return "New chat"
    if len(cleaned) <= 80:
        return cleaned
    return cleaned[:77].rstrip(" .,:;") + "…"


async def _load_conversation(
    db: AsyncSession, organization_id: UUID, user_id: Optional[UUID], conversation_id: UUID,
) -> Conversation:
    """A user only ever reaches their own transcripts inside their organisation."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Conversation not found", "code": "NOT_FOUND"},
        )
    return conversation


def _stored_action(action: KoroActionResponse) -> Dict[str, Any]:
    """Transcript-safe view of an action: no definitions, no generated payloads.

    A few small identifiers survive so replayed chats keep their deep links
    (the run an action produced, the version it created).
    """
    data = action.data or {}
    keep = {
        key: data[key]
        for key in ("execution_id", "retried_execution_id", "version", "restored_from")
        if isinstance(data.get(key), (str, int, float))
    }
    return {
        "type": action.type,
        "status": action.status,
        "detail": action.detail[:1000],
        "workflow_id": action.workflow_id,
        **({"data": keep} if keep else {}),
    }


async def _persist_turn(
    db: AsyncSession, conversation: Conversation, message: str, response: KoroChatResponse,
) -> None:
    """Append one user/assistant exchange to the transcript.

    Generated workflow definitions are not copied into the transcript: the
    workflow itself is saved, and definitions can be large.
    """
    # One transaction shares a single now() in PostgreSQL, so the two rows get
    # explicit, ordered timestamps — transcripts replay in the right order.
    now = datetime.now(timezone.utc)
    conversation.updated_at = now
    if not conversation.title:
        conversation.title = _conversation_title(message)
    db.add(Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=message[:4000],
        metadata={},
        created_at=now,
    ))
    db.add(Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=response.reply[:8000],
        created_at=now + timedelta(milliseconds=1),
        metadata={
            "source": response.source,
            "actions": [_stored_action(action) for action in response.actions],
        },
    ))
    await db.commit()


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


@router.get("/tools")
async def list_koro_tools(
    request: Request,
    domain: str = "",
    kind: str = "",
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """The chat tool catalog: the same table the prompt and parser use."""
    await require_permission("workflow:read")(request, db)
    return tools_payload(domain=domain, kind=kind)


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


@router.get("/runtimes", response_model=LocalModelsResponse)
async def koro_local_models(
    request: Request,
    organization_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Detect local model runtimes and report what is installed.

    Only loopback runtimes are probed, so this is safe to call on a laptop that
    is offline. The response includes download recommendations for Ollama and
    install hints for the other runtimes.
    """
    await require_permission("workflow:read")(request, db)
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)
    runtimes = await local_models.detect_runtimes(config.base_url)
    installed = {
        model["name"]
        for runtime in runtimes
        if runtime.get("reachable")
        for model in runtime.get("models") or []
    }
    return LocalModelsResponse(
        runtimes=runtimes,
        recommended=local_models.recommended_models(),
        hardware=local_models.hardware_info(),
        install=local_models.install_info(),
        configured={
            "base_url": config.base_url,
            "model": config.model,
            "provider": config.provider,
            "local": config.local,
            "ready": bool(config.model and config.model in installed),
            "installed": config.model in installed,
        },
    )


@router.post("/models/pull", response_model=PullJobResponse)
async def koro_pull_model(
    request: Request,
    organization_id: UUID,
    data: PullModelRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Start downloading a model through Ollama's streaming pull API."""
    await require_permission("organization:update")(request, db)
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)
    base_url = data.base_url or config.base_url
    if not local_models.is_local_url(base_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Downloads are only supported for local runtimes",
                "code": "REMOTE_RUNTIME",
            },
        )
    try:
        job = local_models.start_pull(data.model, base_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(exc), "code": "INVALID_MODEL"},
        ) from exc
    return PullJobResponse(**job.as_dict())


@router.get("/models/pull/{job_id}", response_model=PullJobResponse)
async def koro_pull_status(
    request: Request,
    organization_id: UUID,
    job_id: str,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    await require_permission("workflow:read")(request, db)
    job = local_models.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Download job not found", "code": "NOT_FOUND"},
        )
    return PullJobResponse(**job.as_dict())


@router.post("/models/pull/{job_id}/cancel", response_model=PullJobResponse)
async def koro_cancel_pull(
    request: Request,
    organization_id: UUID,
    job_id: str,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    await require_permission("organization:update")(request, db)
    job = local_models.cancel_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Download job not found", "code": "NOT_FOUND"},
        )
    return PullJobResponse(**job.as_dict())


@router.delete("/models")
async def koro_delete_model(
    request: Request,
    organization_id: UUID,
    model: str,
    base_url: Optional[str] = None,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Delete a downloaded model from a local runtime (Ollama only)."""
    await require_permission("organization:update")(request, db)
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)
    target = base_url or config.base_url
    if not local_models.is_local_url(target):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Models can only be deleted from local runtimes",
                "code": "REMOTE_RUNTIME",
            },
        )
    try:
        deleted = await local_models.delete_model(model, target)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(exc), "code": "INVALID_MODEL"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": f"The runtime could not delete the model: {exc}", "code": "RUNTIME_ERROR"},
        ) from exc
    return {"deleted": deleted, "model": model}


@router.post("/models/activate", response_model=KoroSettingsResponse)
async def koro_activate_model(
    request: Request,
    organization_id: UUID,
    data: ActivateModelRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Point the copilot at a model the user just selected or downloaded."""
    await require_permission("organization:update")(request, db)
    organization = await _load_organization(db, organization_id)
    block = dict(_copilot_block(organization))
    block["base_url"] = data.base_url
    block["model"] = data.model
    organization.settings = {**(organization.settings or {}), "copilot": block}
    await db.commit()
    await db.refresh(organization)
    config = _resolve_config(organization)
    return _settings_response(config, await _list_models(config))


@router.post("/models/test", response_model=TestModelResponse)
async def koro_test_model(
    request: Request,
    organization_id: UUID,
    data: TestModelRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Send one tiny completion so the UI can prove a local model runs."""
    await require_permission("workflow:read")(request, db)
    organization = await _load_organization(db, organization_id)
    config = _resolve_config(organization)
    result = await local_models.test_model(
        data.model, data.base_url or config.base_url, api_key=config.api_key
    )
    return TestModelResponse(**result)


@router.post("/providers/models", response_model=ProviderProbeResponse)
async def koro_provider_models(
    request: Request,
    organization_id: UUID,
    data: ProviderProbeRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """List the models an endpoint serves so the chatbox can switch provider.

    The key is used for this one request only — it is not stored here. Saving a
    provider still goes through ``PUT /settings``, which encrypts the key.
    """
    await require_permission("workflow:read")(request, db)
    organization = await _load_organization(db, organization_id)
    current = _resolve_config(organization)
    base_url = data.base_url.strip().rstrip("/")
    # Reuse the stored key when the user is probing the endpoint already saved.
    api_key = (data.api_key or "").strip() or (
        current.api_key if base_url == current.base_url else ""
    )
    provider, local = _describe_endpoint(base_url)
    config = KoroConfig(
        base_url=base_url,
        model=current.model,
        timeout=current.timeout,
        api_key=api_key,
        local=local,
        provider=provider,
    )
    models, error = await _probe_models(config)
    return ProviderProbeResponse(
        ok=error is None,
        provider=provider,
        local=local,
        base_url=base_url,
        models=models[:60],
        error=error,
    )


async def _chat_core(
    request: Request,
    organization_id: UUID,
    data: KoroChatRequest,
    auth_context: Any,
    db: AsyncSession,
) -> KoroChatResponse:
    """Run one assistant turn (shared by the JSON and streaming routes)."""
    await require_permission("workflow:read")(request, db)

    # The full-page assistant keeps a transcript; the in-studio panel does not.
    conversation: Optional[Conversation] = None
    if data.conversation_id is not None:
        conversation = await _load_conversation(
            db, organization_id, auth_context.user_id, data.conversation_id,
        )
    elif data.persist:
        conversation = Conversation(
            organization_id=organization_id,
            user_id=auth_context.user_id,
            title=_conversation_title(data.message),
            metadata={"kind": "assistant"},
        )
        db.add(conversation)
        await db.flush()

    workflows = await _org_workflows(db, organization_id)
    agents = await _org_agents(db, organization_id)
    tools = await _org_tools(db, organization_id)
    organization = await _load_organization(db, organization_id)
    config = _chat_config(
        _resolve_config(organization), data.model, data.base_url,
    )

    models = await _list_models(config)
    source: Literal["model", "commands"] = "commands"
    model_reply = ""

    # The studio sends its unsaved canvas so edits start from what the user sees.
    current_definition: Optional[Dict[str, Any]] = (
        data.definition if isinstance(data.definition, dict) else None
    )
    if current_definition is None and data.workflow_id is not None:
        latest = await _latest_version(db, data.workflow_id)
        if latest is not None and isinstance(latest.definition, dict):
            current_definition = latest.definition

    # Explicit control commands are handled deterministically first: a 0.5B
    # model is unreliable at picking actions, while the parser is exact.
    plan = _command_plan(data.message, workflows, data.workflow_id)
    if plan is None:
        # “confirm <token>” replays the exact action the token was issued for.
        plan = _confirmation_plan(data.message)

    if plan is None and models and config.model in models:
        workflow_context = [
            {"name": w.name, "id": str(w.id), "status": str(getattr(w.status, "value", w.status))}
            for w in workflows[:12]
        ]
        agent_context = [
            {
                "id": str(agent.id),
                "name": agent.name,
                "type": str(getattr(agent.agent_type, "value", agent.agent_type)),
            }
            for agent in agents[:20]
        ]
        tool_context = [
            {"id": str(tool.id), "name": tool.name, "slug": tool.slug}
            for tool in tools[:20]
        ]
        outline = None
        if current_definition is not None:
            outline = {
                "triggers": [
                    {"id": t.get("id"), "type": t.get("type"), "name": t.get("name")}
                    for t in current_definition.get("triggers") or []
                    if isinstance(t, dict)
                ][:4],
                "nodes": [
                    {"id": n.get("id"), "type": n.get("type"), "name": n.get("name")}
                    for n in current_definition.get("nodes") or []
                    if isinstance(n, dict)
                ][:30],
                "edges": [
                    {"from": e.get("from"), "to": e.get("to"), "label": e.get("label")}
                    for e in current_definition.get("edges") or []
                    if isinstance(e, dict)
                ][:40],
            }
        system = (
            _expanded_system_prompt()
            + "\nWorkflows: " + json.dumps(workflow_context)
            + "\nAgents: " + json.dumps(agent_context)
            + "\nTools: " + json.dumps(tool_context)
            + ("\nCurrent workflow: " + json.dumps(outline) if outline else "")
            + _context_block(data.context)
        )
        messages = [
            {"role": "system", "content": system},
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
                if isinstance(parsed.get("agent"), dict) and not action_payload.get("agent"):
                    action_payload["agent"] = parsed["agent"]
                plan = {"reply": str(parsed.get("reply") or ""), "action": action_payload}
                model_reply = str(parsed.get("reply") or "")
                source = "model"
        except Exception:
            plan = None

    if plan is None:
        lowered = data.message.lower()
        # Offline edits append a single, sensible node to the current canvas.
        if current_definition is not None and re.search(
            r"\b(add|also|then|append|change|update|extend|remove|delete)\b", lowered
        ):
            appended = _append_template_node(current_definition, data.message, config.model)
            if appended is not None:
                plan = {
                    "reply": "Added that step to the canvas. Review it and save when you're ready.",
                    "action": {"type": "edit", "definition": appended},
                }
        if plan is None and _looks_like_build_request(data.message):
            definition = _template_definition(data.message, config.model)
            plan = {
                "reply": (
                    "Drafted that automation from your description. Review the nodes, "
                    "then save or test it."
                ),
                "action": {
                    "type": "edit" if data.workflow_id else "build",
                    "definition": definition,
                    "name": _template_name(data.message),
                },
            }
        if plan is None:
            names = ", ".join(w.name for w in workflows[:8]) or "none yet"
            reply = (
                f"I can build, edit, run, publish, retry, list versions, inspect runs, "
                f"create agents and change nodes from here. "
                f"Your workflows: {names}. Try “build a workflow that …” or “create an agent that …”."
                if workflows
                else (
                    "I can build your first automation offline — describe what should "
                    "trigger it and what it should do."
                )
            )
            response = KoroChatResponse(
                reply=reply, source=source if model_reply else "commands",
            )
            if conversation is not None:
                await _persist_turn(db, conversation, data.message, response)
                response.conversation_id = str(conversation.id)
            return response

    action_payload = plan.get("action") or {}
    if not isinstance(action_payload, dict):
        action_payload = {}
    action_type = str(action_payload.get("type") or "none").lower()
    repairs = 0
    if (
        source == "model"
        and action_type == "none"
        and not str(plan.get("reply") or "").strip()
        and _looks_like_build_request(data.message)
    ):
        # A small model sometimes refuses without saying anything useful; fall
        # back to the deterministic generator rather than losing the request.
        action_payload = {
            "type": "edit" if data.workflow_id else "build",
            "definition": _template_definition(data.message, config.model),
            "name": _template_name(data.message),
        }
        action_type = action_payload["type"]
    requested_type = action_type
    mutation_error: Optional[str] = None
    resolved_target: Optional[Workflow] = None
    setting_error: Optional[str] = None
    if (
        action_type == "set_workflow_setting"
        and action_payload.get("error_workflow")
        and not action_payload.get("error_workflow_id")
    ):
        # Names are resolved here, where the org's workflows are already loaded.
        reference = str(action_payload.get("error_workflow") or "").strip()
        matched = (
            next((item for item in workflows if str(item.id) == reference), None)
            or next((item for item in workflows if item.name.lower() == reference.lower()), None)
        )
        if matched is None:
            setting_error = (
                f"I couldn't match the workflow {reference!r} as the error handler."
            )
        else:
            action_payload["error_workflow_id"] = str(matched.id)
    if action_type in NODE_EDIT_TYPES:
        if action_type == "link_subworkflow" and not setting_error:
            # Resolve names here: the tool call carries a workflow name, and
            # the child must be a real workflow in this organization.
            reference = str(
                action_payload.get("sub_workflow")
                or action_payload.get("sub_workflow_name")
                or ""
            ).strip()
            matched = (
                next((item for item in workflows if str(item.id) == reference), None)
                or next(
                    (item for item in workflows if item.name.lower() == reference.lower()),
                    None,
                )
                or next(
                    (
                        item
                        for item in workflows
                        if reference and reference.lower() in item.name.lower()
                    ),
                    None,
                )
            )
            if matched is None:
                setting_error = (
                    f"I couldn't match the workflow {reference!r} to link as a sub-workflow."
                )
            else:
                action_payload["sub_workflow_id"] = str(matched.id)
                parent = _resolve_workflow(
                    workflows,
                    action_payload.get("workflow"),
                    data.workflow_id,
                )
                if parent is not None:
                    cycle = await _subworkflow_cycle(db, parent.id, matched.id)
                    if cycle:
                        setting_error = (
                            "That link would make a sub-workflow cycle ("
                            + " → ".join(
                                next(
                                    (
                                        item.name
                                        for item in workflows
                                        if str(item.id) == step
                                    ),
                                    step,
                                )
                                for step in cycle
                            )
                            + ")."
                        )
        # Typed node/variable/schedule tools edit the open definition and then
        # take the same validate → preview/persist path as a full "edit".
        if current_definition is None and (
            action_payload.get("workflow") or data.workflow_id
        ):
            # No canvas is open (full-page chat): edit the named workflow's
            # latest saved definition instead of asking the user to open it.
            resolved_target = _resolve_workflow(
                workflows, action_payload.get("workflow"), data.workflow_id,
            )
            if resolved_target is not None:
                latest = await _latest_version(db, resolved_target.id)
                if latest is not None and isinstance(latest.definition, dict):
                    current_definition = latest.definition
        updated, mutation_error = _mutate_definition(current_definition, action_payload)
        if setting_error:
            updated, mutation_error = None, setting_error
        if updated is not None:
            action_payload = {
                "type": "edit",
                "definition": updated,
                "workflow": action_payload.get("workflow") or (
                    str(resolved_target.id) if resolved_target is not None else None
                ),
            }
            action_type = "edit"

    if source == "model" and action_type in ("build", "edit"):
        action_payload, repairs = await _repair_action(action_payload, data.message, config)

    actions: List[KoroActionResponse] = []

    if mutation_error:
        actions.append(KoroActionResponse(
            type=requested_type, status="failed", detail=mutation_error,
            workflow_id=str(data.workflow_id) if data.workflow_id else None,
        ))
    elif action_type == "batch":
        if data.preview:
            actions.append(KoroActionResponse(
                type="batch", status="failed",
                detail="Batch actions cannot be previewed; apply them one at a time.",
            ))
        else:
            sub_actions = action_payload.get("actions")
            if not isinstance(sub_actions, list) or not sub_actions:
                actions.append(KoroActionResponse(
                    type="batch", status="failed",
                    detail="A batch needs at least one action.",
                ))
            else:
                for sub_action in sub_actions[:6]:
                    if not isinstance(sub_action, dict):
                        continue
                    if str(sub_action.get("type") or "").lower() == "batch":
                        continue
                    try:
                        actions.append(await _apply_action(
                            db=db,
                            request=request,
                            organization_id=organization_id,
                            user_id=auth_context.user_id,
                            action=sub_action,
                            workflows=workflows,
                            current_id=data.workflow_id,
                            trigger_input=data.input,
                        ))
                    except HTTPException as exc:
                        detail = (
                            exc.detail.get("error")
                            if isinstance(exc.detail, dict) else str(exc.detail)
                        )
                        actions.append(KoroActionResponse(
                            type=str(sub_action.get("type") or "none"),
                            status="failed",
                            detail=f"You don't have permission: {detail}",
                        ))
                if not actions:
                    actions.append(KoroActionResponse(
                        type="batch", status="failed",
                        detail="None of the requested actions could be executed.",
                    ))
    # Preview: validate and hand the definition back for the canvas to apply.
    elif data.preview and action_type in ("build", "edit"):
        definition = _prepare_definition(action_payload.get("definition"))
        if definition is None:
            action = KoroActionResponse(
                type=action_type,
                status="failed",
                detail=(
                    "I need a complete workflow definition to do that. Describe the "
                    "trigger and the steps you want."
                ),
            )
        else:
            verdict = validate_definition(definition)
            if not verdict.valid:
                action = KoroActionResponse(
                    type=action_type,
                    status="failed",
                    detail=_definition_errors(verdict),
                )
            else:
                preview_data: Dict[str, Any] = {
                    "definition": definition,
                    "preview": True,
                }
                if action_type == "build":
                    # Only a brand-new draft gets a generated name; edits keep
                    # the workflow the user is already working on.
                    preview_data["name"] = _clean_name(action_payload.get("name"))
                action = KoroActionResponse(
                    type=action_type,
                    status="ok",
                    detail=(
                        f"Prepared a workflow with {len(definition['nodes'])} node(s) and "
                        f"{len(definition['edges'])} connection(s). Apply it to the canvas, "
                        "then save or test it."
                    ),
                    workflow_id=str(data.workflow_id) if data.workflow_id else (
                        str(resolved_target.id) if resolved_target is not None else None
                    ),
                    data=preview_data,
                )
        actions.append(action)
    else:
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
        # Every mutating tool advertises how to undo itself (Phase 13).
        spec = TOOL_BY_NAME.get(action.type)
        if spec is not None and spec.returns_undo and action.status == "ok":
            action.data = {
                **(action.data or {}),
                "undo": {"action": "undo_last", "workflow_id": action.workflow_id},
            }
        actions.append(action)

    if repairs and actions and actions[0].status == "ok":
        actions[0].data = {**(actions[0].data or {}), "repaired": repairs}

    failed_actions = [item for item in actions if item.status == "failed"]
    if failed_actions:
        reply = " ".join(item.detail for item in failed_actions[:2])
    elif len(actions) == 1:
        action = actions[0]
        reply = str(plan.get("reply") or "").strip() or action.detail
        if TOOL_BY_NAME.get(action.type) is not None:
            # Every registered tool's detail carries the concrete result
            # (nodes, version, diagnosis, node logs, ...).
            reply = action.detail
    else:
        reply = str(plan.get("reply") or "").strip() or " ".join(
            item.detail for item in actions[:3]
        )
    response = KoroChatResponse(
        reply=reply,
        source=source,
        actions=actions,
        workflow_id=next(
            (item.workflow_id for item in actions if item.workflow_id), None,
        ),
    )
    if conversation is not None:
        await _persist_turn(db, conversation, data.message, response)
        response.conversation_id = str(conversation.id)
    return response


# ---------------------------------------------------------------------------
# Chat history (the full-page assistant)
# ---------------------------------------------------------------------------

@router.get("/conversations", response_model=List[ConversationSummary])
async def koro_conversations(
    request: Request,
    organization_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """The caller's assistant transcripts, most recently active first."""
    await require_permission("workflow:read")(request, db)
    counts = (
        select(Message.conversation_id, func.count(Message.id).label("message_count"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = await db.execute(
        select(Conversation, func.coalesce(counts.c.message_count, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(
            Conversation.organization_id == organization_id,
            Conversation.user_id == auth_context.user_id,
            Conversation.deleted_at.is_(None),
            Conversation.status != ConversationStatus.DELETED,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return [
        ConversationSummary(
            id=str(conversation.id),
            title=conversation.title or "New chat",
            message_count=int(count or 0),
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for conversation, count in rows.all()
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def koro_conversation(
    request: Request,
    organization_id: UUID,
    conversation_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """One transcript with its messages, ready to replay in the assistant."""
    await require_permission("workflow:read")(request, db)
    conversation = await _load_conversation(
        db, organization_id, auth_context.user_id, conversation_id,
    )
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(200)
    )
    messages: List[ConversationMessage] = []
    for message in result.scalars().all():
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        stored_actions = metadata.get("actions")
        messages.append(ConversationMessage(
            id=str(message.id),
            role=message.role.value if hasattr(message.role, "value") else str(message.role),
            content=message.content,
            created_at=message.created_at,
            source=str(metadata["source"]) if metadata.get("source") else None,
            actions=[
                item for item in stored_actions or [] if isinstance(item, dict)
            ] if isinstance(stored_actions, list) else [],
        ))

    count = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
    )
    return ConversationDetail(
        id=str(conversation.id),
        title=conversation.title or "New chat",
        message_count=int(count.scalar_one() or 0),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages,
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def koro_rename_conversation(
    request: Request,
    organization_id: UUID,
    conversation_id: UUID,
    data: ConversationRename,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    await require_permission("workflow:read")(request, db)
    conversation = await _load_conversation(
        db, organization_id, auth_context.user_id, conversation_id,
    )
    conversation.title = re.sub(r"\s+", " ", data.title).strip()[:200]
    await db.commit()
    await db.refresh(conversation)
    count = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
    )
    return ConversationSummary(
        id=str(conversation.id),
        title=conversation.title or "New chat",
        message_count=int(count.scalar_one() or 0),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/conversations/{conversation_id}")
async def koro_delete_conversation(
    request: Request,
    organization_id: UUID,
    conversation_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Archive a transcript. Messages are kept (soft delete) for audit."""
    await require_permission("workflow:read")(request, db)
    conversation = await _load_conversation(
        db, organization_id, auth_context.user_id, conversation_id,
    )
    conversation.deleted_at = datetime.now(timezone.utc)
    conversation.status = ConversationStatus.DELETED
    await db.commit()
    return {"deleted": True, "conversation_id": str(conversation.id)}


@router.post("/chat", response_model=KoroChatResponse)
async def koro_chat(
    request: Request,
    organization_id: UUID,
    data: KoroChatRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Ask Koro to operate a workflow, locally and offline-first."""
    return await _chat_core(request, organization_id, data, auth_context, db)


@router.post("/chat/stream")
async def koro_chat_stream(
    request: Request,
    organization_id: UUID,
    data: KoroChatRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Same turn as ``/chat``, streamed as SSE deltas for the chat thread.

    The turn is computed once (actions, persistence) and the reply text is sent
    in small deltas followed by the complete result, so the UI can render it
    progressively and still receive actions and the conversation id.
    """
    result = await _chat_core(request, organization_id, data, auth_context, db)

    async def events():
        text = result.reply or ""
        chunk = 28
        for start in range(0, len(text), chunk):
            payload = {"type": "delta", "text": text[start:start + chunk]}
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.008)
        final = {"type": "final", "result": result.model_dump(mode="json")}
        yield f"data: {json.dumps(final)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
