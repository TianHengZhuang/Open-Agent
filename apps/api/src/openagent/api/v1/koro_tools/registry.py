"""Koro chat tools: one table, three consumers.

`TOOL_SCHEMAS` is the single source of truth for what the assistant can do:

* the router's accepted action types (`koro.ACTION_TYPES`),
* the model system prompt's action list,
* `GET /api/v1/koro/tools` (and the docs page built from it).

A tool that is not in this table cannot be planned, described or listed, so the
four surfaces cannot drift apart. `tests/test_koro_tool_registry.py` audits the
table and the prompt together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

DOMAINS = (
    "workflow",
    "nodes",
    "settings",
    "runs",
    "credentials",
    "agents",
    "operations",
)

KINDS = ("read", "write", "destructive")


@dataclass(frozen=True)
class ToolSpec:
    """One chat tool: what it is called, who may call it, and its arguments."""

    name: str
    domain: str
    kind: str
    permission: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    returns_undo: bool = False

    def schema(self) -> Dict[str, Any]:
        """JSON schema of the action the assistant sends back."""
        properties = {"type": {"const": self.name}, **self.params}
        return {
            "type": "object",
            "properties": properties,
            "required": ["type"],
            "additionalProperties": True,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "kind": self.kind,
            "permission": self.permission,
            "description": self.description,
            "requires_confirmation": self.requires_confirmation,
            "returns_undo": self.returns_undo,
            "schema": self.schema(),
        }


# ---------------------------------------------------------------------------
# Shared parameter fragments
# ---------------------------------------------------------------------------

WORKFLOW = {
    "workflow": {
        "type": "string",
        "description": "Workflow name or id (defaults to the open workflow).",
    }
}
NAME = {"name": {"type": "string", "description": "Workflow or item name."}}
DEFINITION = {
    "definition": {
        "type": "object",
        "description": "Complete workflow definition (schema 1.1).",
    }
}
VERSION = {"version": {"type": "string", "description": "Version label, e.g. v3."}}
EXECUTION = {"execution_id": {"type": "string", "description": "Execution id."}}
RUN_INPUT = {
    "input": {"type": "object", "description": "Trigger input for the run."}
}
NODE = {"node": {"type": "string", "description": "Node id or name."}}


TOOL_SCHEMAS: Tuple[ToolSpec, ...] = (
    # ---- workflow -------------------------------------------------------
    ToolSpec(
        "list", "workflow", "read", "workflow:read",
        "List the organization's workflows with status.",
    ),
    ToolSpec(
        "inspect", "workflow", "read", "workflow:read",
        "Show the latest execution of a workflow and its node statuses.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "explain", "workflow", "read", "workflow:read",
        "Explain what a workflow does, node by node.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "diagnose", "workflow", "read", "workflow:read",
        "Diagnose the last failed run and point at the failing node.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "create", "workflow", "write", "workflow:create",
        "Create an empty draft workflow with a starter definition.",
        {**NAME},
    ),
    ToolSpec(
        "build", "workflow", "write", "workflow:create",
        "Build a new workflow from a complete definition.",
        {**NAME, **DEFINITION}, returns_undo=True,
    ),
    ToolSpec(
        "edit", "workflow", "write", "workflow:update",
        "Replace the open workflow's definition with a new one.",
        {**WORKFLOW, **DEFINITION}, returns_undo=True,
    ),
    ToolSpec(
        "update_workflow", "workflow", "write", "workflow:update",
        "Rename a workflow or change its description.",
        {
            **WORKFLOW,
            "name": {"type": "string", "description": "New name."},
            "description": {"type": "string", "description": "New description."},
        },
    ),
    ToolSpec(
        "duplicate", "workflow", "write", "workflow:create",
        "Copy a workflow into a new draft.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "delete", "workflow", "destructive", "workflow:delete",
        "Soft-delete a workflow; run history is kept for audit.",
        {**WORKFLOW}, requires_confirmation=True,
    ),
    ToolSpec(
        "publish", "workflow", "write", "workflow:update",
        "Activate the workflow so its webhooks and schedules run.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "unpublish", "workflow", "write", "workflow:update",
        "Return an active workflow to draft and stop its triggers.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "versions", "workflow", "read", "workflow:read",
        "List saved versions (newest first).",
        {**WORKFLOW},
    ),
    ToolSpec(
        "restore", "workflow", "destructive", "workflow:update",
        "Restore a saved version as a new draft version.",
        {**WORKFLOW, **VERSION}, requires_confirmation=True, returns_undo=True,
    ),
    ToolSpec(
        "undo_last", "workflow", "write", "workflow:update",
        "Undo the last version change by restoring the previous version.",
        {**WORKFLOW}, returns_undo=True,
    ),
    ToolSpec(
        "export_workflow", "workflow", "read", "workflow:read",
        "Export a workflow's latest definition as JSON.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "import_workflow", "workflow", "write", "workflow:create",
        "Create a workflow from an exported definition.",
        {**NAME, **DEFINITION}, returns_undo=True,
    ),
    # ---- nodes ----------------------------------------------------------
    ToolSpec(
        "add_node", "nodes", "write", "workflow:update",
        "Add a node to the open workflow.",
        {
            **WORKFLOW,
            "node_type": {"type": "string"},
            "name": {"type": "string"},
            "config": {"type": "object"},
            "connect_from": {"type": "string"},
        },
        returns_undo=True,
    ),
    ToolSpec(
        "update_node", "nodes", "write", "workflow:update",
        "Change a node's name or config.",
        {**WORKFLOW, **NODE, "name": {"type": "string"}, "config": {"type": "object"}},
        returns_undo=True,
    ),
    ToolSpec(
        "remove_node", "nodes", "write", "workflow:update",
        "Remove a node and its connections.",
        {**WORKFLOW, **NODE}, returns_undo=True,
    ),
    ToolSpec(
        "connect", "nodes", "write", "workflow:update",
        "Connect two nodes (optionally into a named port).",
        {**WORKFLOW, "from": {"type": "string"}, "to": {"type": "string"}, "label": {"type": "string"}},
        returns_undo=True,
    ),
    ToolSpec(
        "disconnect", "nodes", "write", "workflow:update",
        "Remove the connection between two nodes.",
        {**WORKFLOW, "from": {"type": "string"}, "to": {"type": "string"}},
        returns_undo=True,
    ),
    ToolSpec(
        "test_node", "nodes", "write", "workflow:execute",
        "Execute one node with upstream data and return its output.",
        {**WORKFLOW, **NODE, "input": {"type": "object"}},
    ),
    ToolSpec(
        "node_coverage", "nodes", "read", "workflow:read",
        "Answer 'can you do X?' with the node tier map and honest gaps.",
        {"query": {"type": "string", "description": "Capability or node name."}},
    ),
    ToolSpec(
        "link_subworkflow", "nodes", "write", "workflow:update",
        "Link a Subworkflow node to another workflow (cycle-safe).",
        {**WORKFLOW, "sub_workflow": {"type": "string"}}, returns_undo=True,
    ),
    # ---- settings -------------------------------------------------------
    ToolSpec(
        "set_workflow_setting", "settings", "write", "workflow:update",
        "Set concurrency, execution timeout, save-data mode or the error workflow.",
        {
            **WORKFLOW,
            "max_concurrency": {"type": "integer", "minimum": 0, "maximum": 32},
            "execution_timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
            "save_execution_data": {"type": "string", "enum": ["all", "none", "on_error"]},
            "error_workflow": {"type": "string"},
            "clear_error_workflow": {"type": "boolean"},
        },
        returns_undo=True,
    ),
    ToolSpec(
        "set_node_settings", "settings", "write", "workflow:update",
        "Set retry, onError, alwaysOutputData, executeOnce, timeout or disabled on a node.",
        {**WORKFLOW, **NODE}, returns_undo=True,
    ),
    ToolSpec(
        "schedule", "settings", "write", "workflow:update",
        "Set the workflow's cron schedule (alias of set_schedule).",
        {**WORKFLOW, "cron": {"type": "string"}}, returns_undo=True,
    ),
    ToolSpec(
        "set_schedule", "settings", "write", "workflow:update",
        "Set the schedule trigger's cron expression.",
        {**WORKFLOW, "cron": {"type": "string"}}, returns_undo=True,
    ),
    ToolSpec(
        "set_static_data", "settings", "write", "workflow:update",
        "Write workflow static data for the global or node scope.",
        {**WORKFLOW, "scope": {"type": "string"}, "values": {"type": "object"}},
        returns_undo=True,
    ),
    ToolSpec(
        "variables", "settings", "read", "workflow:read",
        "List the workflow's declared variables and their values.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "set_variable", "settings", "write", "workflow:update",
        "Declare or change workflow variables.",
        {**WORKFLOW, "variables": {"type": "object"}}, returns_undo=True,
    ),
    # ---- runs -----------------------------------------------------------
    ToolSpec(
        "run", "runs", "write", "workflow:execute",
        "Run the workflow now and report the summary.",
        {**WORKFLOW, **RUN_INPUT},
    ),
    ToolSpec(
        "runs", "runs", "read", "execution:read",
        "List recent runs (all workflows, or one workflow).",
        {**WORKFLOW, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
    ),
    ToolSpec(
        "node_logs", "runs", "read", "execution:read",
        "Per-node logs (status, error, duration, items) of one execution.",
        {**WORKFLOW, **EXECUTION, **NODE},
    ),
    ToolSpec(
        "retry", "runs", "write", "workflow:execute",
        "Re-run a previous execution.",
        {**WORKFLOW, **EXECUTION},
    ),
    ToolSpec(
        "cancel", "runs", "write", "workflow:execute",
        "Cancel a running or waiting execution.",
        {**WORKFLOW, **EXECUTION},
    ),
    ToolSpec(
        "resume_wait", "runs", "write", "workflow:execute",
        "Resume an execution parked by a Wait node.",
        {**WORKFLOW, **EXECUTION},
    ),
    # ---- credentials ----------------------------------------------------
    ToolSpec(
        "list_credentials", "credentials", "read", "credential:read",
        "List credential names and providers (values never leave the vault).",
    ),
    # ---- agents ---------------------------------------------------------
    ToolSpec(
        "list_agents", "agents", "read", "agent:read",
        "List agents with their type and status.",
    ),
    ToolSpec(
        "create_agent", "agents", "write", "agent:create",
        "Create an agent with instructions, model and tools.",
        {"agent": {"type": "object", "description": "name, description, instructions, agent_type, model, tools"}},
    ),
    # ---- operations -----------------------------------------------------
    ToolSpec(
        "webhook_url", "operations", "read", "workflow:read",
        "Show the resolved production and test webhook URLs.",
        {**WORKFLOW},
    ),
    ToolSpec(
        "operations_status", "operations", "read", "execution:read",
        "Running/queued counts, failure rate and whether queue mode is on.",
    ),
    ToolSpec(
        "prune", "operations", "destructive", "workflow:update",
        "Delete this organization's executions past the retention window.",
        {
            "retention_seconds": {"type": "integer", "minimum": 60},
            "keep_last": {"type": "integer", "minimum": 0},
        },
        requires_confirmation=True,
    ),
    ToolSpec(
        "health", "operations", "read", "workflow:read",
        "Platform health snapshot (components, status, notes).",
    ),
    ToolSpec(
        "open_page", "operations", "read", "workflow:read",
        "Resolve the in-app page for a workflow, a run, or a workspace surface.",
        {
            "page": {
                "type": "string",
                "enum": [
                    "assistant", "workflows", "runs", "settings", "account",
                    "workflow", "execution",
                ],
            },
            "workflow_id": {"type": "string"},
            "execution_id": {"type": "string"},
        },
    ),
)

TOOL_BY_NAME: Dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SCHEMAS}

ACTION_NAMES: Tuple[str, ...] = tuple(spec.name for spec in TOOL_SCHEMAS)

#: Actions that are routed by the chat loop but are not autonomous tools.
RESERVED_ACTIONS: Tuple[str, ...] = ("none", "batch")


def tools_payload(*, domain: str = "", kind: str = "") -> Dict[str, Any]:
    """The `GET /koro/tools` body, filtered by domain and/or kind."""
    wanted_domain = (domain or "").strip().lower()
    wanted_kind = (kind or "").strip().lower()
    selected = [
        spec
        for spec in TOOL_SCHEMAS
        if (not wanted_domain or spec.domain == wanted_domain)
        and (not wanted_kind or spec.kind == wanted_kind)
    ]
    return {
        "tools": [spec.to_dict() for spec in selected],
        "total": len(TOOL_SCHEMAS),
        "returned": len(selected),
        "domains": list(DOMAINS),
        "kinds": list(KINDS),
        "reserved": list(RESERVED_ACTIONS),
    }


def prompt_union() -> str:
    """The `type` union embedded in the system prompt."""
    return "|".join(ACTION_NAMES) + "|" + "|".join(RESERVED_ACTIONS)


def domain_tools(domains: Iterable[str]) -> List[ToolSpec]:
    wanted = {domain.strip().lower() for domain in domains}
    return [spec for spec in TOOL_SCHEMAS if spec.domain in wanted]


__all__ = [
    "ACTION_NAMES",
    "DOMAINS",
    "KINDS",
    "RESERVED_ACTIONS",
    "TOOL_BY_NAME",
    "TOOL_SCHEMAS",
    "ToolSpec",
    "domain_tools",
    "prompt_union",
    "tools_payload",
]
