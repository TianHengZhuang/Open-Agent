"""Canonical workflow definition model (schema 1.1).

The JSON definition remains the persisted contract; this module provides the
typed model, the additive 1.0 -> 1.1 migration and a canonical workflow
document that combines workflow-row metadata with its definition.

Design rules:

* 1.0 definitions stay valid forever. Normalization only adds keys and bumps
  ``schema_version``; it never removes or rewrites author data.
* Unknown keys are preserved (``extra="allow"``) so future/unnamed fields
  survive a read/validate/write cycle.
* Nothing here touches the database. The CRUD API and validator import it.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.1"
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1")

TriggerType = Literal["manual", "webhook", "schedule", "event", "chat"]
EdgeWhen = Literal["always", "success", "failure"]
VariableType = Literal["string", "number", "boolean", "json"]


class Position(BaseModel):
    model_config = ConfigDict(extra="allow")
    x: float
    y: float


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")
    max_attempts: int = Field(default=0, ge=0, le=10)


class WorkflowTriggerModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: TriggerType
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    position: Optional[Position] = None


class WorkflowNodeModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: str
    type_version: Optional[int] = Field(default=None, ge=1)
    name: str
    position: Optional[Position] = None
    config: dict[str, Any] = Field(default_factory=dict)
    retry_policy: Optional[RetryPolicy] = None
    timeout_seconds: Optional[float] = Field(default=None, gt=0)
    approval_required: Optional[bool] = None
    disabled: Optional[bool] = None
    notes: Optional[str] = None
    group_id: Optional[str] = None


class EdgeConditionModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    when: EdgeWhen = "always"
    expression: Optional[str] = None


class WorkflowEdgeModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    from_: str = Field(alias="from")
    to: str
    from_port: Optional[str] = None
    to_port: Optional[str] = None
    connection_type: Literal["main", "error"] = "main"
    label: Optional[str] = None
    condition: Optional[EdgeConditionModel] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowVariableModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: VariableType
    default: Any = None
    required: bool = False
    description: Optional[str] = None


class StickyNoteModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    x: float
    y: float
    width: float
    height: float
    color: str
    text: str


class WorkflowSettingsModel(BaseModel):
    """Execution/layout settings. Unknown settings are preserved."""

    model_config = ConfigDict(extra="allow")
    timezone: Optional[str] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=32)
    execution_timeout_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    # n8n's "Save execution data": all (default) | none | on_error.
    save_execution_data: Optional[str] = Field(
        default=None, pattern="^(all|none|on_error)$"
    )
    # n8n's workflow-level "On error" handler run when this workflow fails.
    error_workflow_id: Optional[str] = None
    sticky_notes: list[StickyNoteModel] = Field(default_factory=list)
    pinned_data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Data-flow contract (1.1). Types only: the current engine is sequential and
# does not consume items yet. Prompt 08 owns item execution.
# ---------------------------------------------------------------------------

class BinaryReferenceModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    mime_type: str
    file_name: Optional[str] = None
    data_ref: str
    size_bytes: Optional[int] = Field(default=None, ge=0)


class DataItemErrorModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: Optional[str] = None
    message: str
    retriable: Optional[bool] = None


class DataItemModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    json: dict[str, Any] = Field(default_factory=dict)
    binary: dict[str, BinaryReferenceModel] = Field(default_factory=dict)
    paired_item: Optional[int | list[int]] = None
    error: Optional[DataItemErrorModel] = None


class PortPayloadModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    port: str
    connection_type: Literal["main", "error"] = "main"
    items: list[DataItemModel] = Field(default_factory=list)


class ExecutionTraceEventModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    execution_id: str
    node_id: str
    node_name: Optional[str] = None
    port: Optional[str] = None
    connection_type: Literal["main", "error"] = "main"
    item_index: Optional[int] = Field(default=None, ge=0)
    attempt: Optional[int] = Field(default=None, ge=1)
    status: Literal["running", "succeeded", "failed", "skipped", "waiting"]
    duration_ms: Optional[int] = Field(default=None, ge=0)
    input_items: Optional[int] = Field(default=None, ge=0)
    output_items: Optional[int] = Field(default=None, ge=0)
    error: Optional[DataItemErrorModel] = None
    ts: str


class WorkflowDefinitionModel(BaseModel):
    """Canonical definition. 1.0 documents validate without migration."""

    model_config = ConfigDict(extra="allow")
    schema_version: str = SCHEMA_VERSION
    triggers: list[WorkflowTriggerModel] = Field(default_factory=list)
    nodes: list[WorkflowNodeModel] = Field(default_factory=list)
    edges: list[WorkflowEdgeModel] = Field(default_factory=list)
    variables: list[WorkflowVariableModel] = Field(default_factory=list)
    settings: WorkflowSettingsModel = Field(default_factory=WorkflowSettingsModel)
    # Additive 1.1 fields -------------------------------------------------
    error_workflow_id: Optional[str] = None
    static_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalWorkflowModel(BaseModel):
    """Workflow-row metadata plus its one canonical definition.

    Name/description/tags/status are stored on the ``workflows`` row; the
    definition carries graph, variables, settings, error workflow, static data
    and free-form metadata. This model is the single in-memory representation
    used by export/import and future chat planners.
    """

    model_config = ConfigDict(extra="allow")
    id: Optional[UUID] = None
    name: str
    description: Optional[str] = None
    version: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    active: bool = False
    definition: WorkflowDefinitionModel


def normalize_definition(definition: Any) -> Any:
    """Upgrade a supported definition to 1.1 additively.

    Returns a new dict; the input is never mutated. Unsupported or malformed
    input is returned unchanged so callers can report the original error.
    """
    if not isinstance(definition, dict):
        return definition
    version = definition.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return definition

    normalized = deepcopy(definition)
    if version == "1.0":
        normalized["schema_version"] = SCHEMA_VERSION
    normalized.setdefault("static_data", {})
    normalized.setdefault("metadata", {})
    settings = normalized.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        normalized["settings"] = settings
    # 1.1 defaults that are safe to materialize. Execution timeout is left
    # absent when disabled so existing engine defaults apply unchanged.
    settings.setdefault("sticky_notes", [])
    settings.setdefault("pinned_data", {})
    return normalized


def migration_required(definition: Any) -> bool:
    return (
        isinstance(definition, dict)
        and definition.get("schema_version") != SCHEMA_VERSION
        and definition.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS
    )


def parse_definition(definition: Any) -> WorkflowDefinitionModel:
    """Validate shape into the typed model. Raises pydantic ValidationError."""
    return WorkflowDefinitionModel.model_validate(definition)


def canonical_workflow(
    *,
    definition: Any,
    name: str,
    description: Optional[str] = None,
    workflow_id: Optional[UUID] = None,
    version: Optional[str] = None,
    tags: Optional[list[str]] = None,
    active: bool = False,
) -> CanonicalWorkflowModel:
    return CanonicalWorkflowModel(
        id=workflow_id,
        name=name,
        description=description,
        version=version,
        tags=list(tags or []),
        active=active,
        definition=parse_definition(normalize_definition(definition)),
    )
