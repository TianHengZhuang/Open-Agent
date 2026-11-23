"""Canonical node registry API (shared by the studio, integrations and Koro)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.api.dependencies import get_current_org_context, require_permission
from openagent.db.session import get_db
from openagent.registry import node_registry
from openagent.schemas.base import ApiErrorResponse

router = APIRouter(prefix="/organizations/{organization_id}/nodes", tags=["nodes"])


class NodeRegistryResponse(BaseModel):
    registry_version: str
    generated_from: str
    nodes: List[Dict[str, Any]]
    triggers: List[Dict[str, Any]]


class NodeParameterValidationRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    type_version: Optional[int] = Field(default=None, ge=1)


class NodeParameterValidationResponse(BaseModel):
    valid: bool
    issues: List[Dict[str, Any]]
    type_version: Optional[int] = None


def _port_payload(port: node_registry.PortDefinition) -> Dict[str, Any]:
    return {
        "name": port.name,
        "label": port.label,
        "direction": port.direction,
        "data_types": list(port.data_types),
        "required": port.required,
        "multiple": port.multiple,
        "description": port.description,
        "default_value": port.default_value,
        "dynamic": port.dynamic,
    }


def _registry_or_503() -> node_registry.NodeRegistry:
    try:
        return node_registry.load_registry()
    except node_registry.RegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": str(exc), "code": "NODE_REGISTRY_UNAVAILABLE"},
        ) from exc


@router.get(
    "/registry",
    response_model=NodeRegistryResponse,
    responses={401: {"model": ApiErrorResponse}, 403: {"model": ApiErrorResponse}},
)
async def get_node_registry(
    request: Request,
    organization_id: UUID,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Return the full generated registry (definitions, ports, fields, versions)."""
    await require_permission("workflow:read")(request, db)
    registry = _registry_or_503()
    document = node_registry.registry_document()
    return NodeRegistryResponse(
        registry_version=registry.registry_version,
        generated_from=registry.generated_from,
        nodes=document.get("nodes", []),
        triggers=document.get("triggers", []),
    )


@router.get(
    "/registry/{node_type}",
    response_model=Dict[str, Any],
    responses={
        401: {"model": ApiErrorResponse},
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
    },
)
async def get_node_definition(
    request: Request,
    organization_id: UUID,
    node_type: str,
    version: Optional[int] = Query(default=None, ge=1),
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """One node definition, optionally pinned to a registry version."""
    await require_permission("workflow:read")(request, db)
    definition = node_registry.node_definition(node_type, version)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": f"Unknown node type {node_type!r}"
                + (f" at version {version}" if version is not None else ""),
                "code": "NODE_TYPE_NOT_FOUND",
            },
        )
    registry = node_registry.load_registry()
    return {
        "type": definition.type,
        "version": definition.version,
        "latest_version": registry.latest.get(definition.type),
        "label": definition.label,
        "description": definition.description,
        "category": definition.category,
        "source": definition.source,
        "outputs": list(definition.outputs),
        "has_input": definition.has_input,
        "input_ports": [_port_payload(p) for p in definition.input_ports],
        "output_ports": [_port_payload(p) for p in definition.output_ports],
        "fan_in": definition.fan_in,
        "terminal": definition.terminal,
        "capabilities": list(definition.capabilities),
        "documentation_url": definition.documentation_url,
        "default_config": definition.default_config,
        "config_fields": [
            {
                "key": f.key,
                "label": f.label,
                "type": f.type,
                "required": f.required,
                "sensitive": f.sensitive,
                "placeholder": f.placeholder,
                "help": f.help,
                "options": list(f.options),
                "min": f.min,
                "max": f.max,
            }
            for f in definition.config_fields
        ],
    }


@router.post(
    "/registry/{node_type}/validate",
    response_model=NodeParameterValidationResponse,
    responses={
        401: {"model": ApiErrorResponse},
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
    },
)
async def validate_node_parameters(
    request: Request,
    organization_id: UUID,
    node_type: str,
    data: NodeParameterValidationRequest,
    auth_context=Depends(get_current_org_context),
    db: AsyncSession = Depends(get_db),
):
    """Validate a config against the registry (used by the AI planner and UI)."""
    await require_permission("workflow:update")(request, db)
    definition = node_registry.node_definition(node_type, data.type_version)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": f"Unknown node type {node_type!r}",
                "code": "NODE_TYPE_NOT_FOUND",
            },
        )
    issues = node_registry.validate_node_parameters(node_type, data.config, data.type_version)
    return NodeParameterValidationResponse(
        valid=not any(i.get("severity") == "error" for i in issues),
        issues=issues,
        type_version=definition.version,
    )
