"""Canonical node registry shared by the backend, the frontend and Koro.

The registry is generated from the single authoring source,
``apps/web/src/features/workflows/node-catalog.ts``, by
``apps/web/src/features/workflows/registry.test.ts`` (run with
``WRITE_NODE_REGISTRY=1``). The generated JSON is shipped inside this package
so the API can load it without Node.js, and a second copy lives next to the
web catalog for runtime validation. A web test fails when either copy drifts.

This module is pure (no DB, no network) so the definition validator can use it
at import time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

REGISTRY_PATH = Path(__file__).resolve().with_name("node-registry.json")


class RegistryError(RuntimeError):
    """The generated registry is missing or structurally invalid."""


@dataclass(frozen=True)
class RegistryField:
    key: str
    label: str
    type: str
    required: bool = False
    sensitive: bool = False
    placeholder: Optional[str] = None
    help: Optional[str] = None
    options: tuple[dict[str, str], ...] = ()
    min: Optional[float] = None
    max: Optional[float] = None


@dataclass(frozen=True)
class PortDefinition:
    name: str
    label: str
    direction: str
    data_types: tuple[str, ...]
    required: bool = False
    multiple: bool = False
    description: Optional[str] = None
    default_value: Any = None
    dynamic: bool = False


@dataclass(frozen=True)
class NodeDefinition:
    type: str
    version: int
    label: str
    description: str
    category: str
    source: str
    outputs: tuple[str, ...]
    has_input: bool
    input_ports: tuple[PortDefinition, ...]
    output_ports: tuple[PortDefinition, ...]
    fan_in: bool
    terminal: bool
    capabilities: tuple[str, ...]
    documentation_url: Optional[str]
    default_config: dict[str, Any]
    config_fields: tuple[RegistryField, ...]


@dataclass(frozen=True)
class TriggerDefinition:
    type: str
    version: int
    label: str
    description: str
    config_fields: tuple[RegistryField, ...]
    output_ports: tuple[PortDefinition, ...]


@dataclass(frozen=True)
class NodeRegistry:
    registry_version: str
    generated_from: str
    nodes: dict[tuple[str, int], NodeDefinition]
    latest: dict[str, int]
    triggers: dict[str, TriggerDefinition]
    raw: dict[str, Any]

    def node(self, node_type: str, version: Optional[int] = None) -> Optional[NodeDefinition]:
        if version is not None:
            return self.nodes.get((node_type, version))
        latest = self.latest.get(node_type)
        if latest is None:
            return None
        return self.nodes.get((node_type, latest))

    def versions(self, node_type: str) -> list[int]:
        return sorted(v for (t, v) in self.nodes if t == node_type)


def _field(payload: Any) -> RegistryField:
    if not isinstance(payload, dict) or not isinstance(payload.get("key"), str):
        raise RegistryError("Registry field entries require a string 'key'.")
    options = payload.get("options") or []
    if not isinstance(options, list):
        raise RegistryError(f"Field {payload['key']!r} options must be a list.")
    return RegistryField(
        key=payload["key"],
        label=str(payload.get("label") or payload["key"]),
        type=str(payload.get("type") or "text"),
        required=bool(payload.get("required")),
        sensitive=bool(payload.get("sensitive")),
        placeholder=payload.get("placeholder"),
        help=payload.get("help"),
        options=tuple(
            {"value": str(o.get("value")), "label": str(o.get("label") or o.get("value"))}
            for o in options
            if isinstance(o, dict) and o.get("value") is not None
        ),
        min=payload.get("min") if isinstance(payload.get("min"), (int, float)) else None,
        max=payload.get("max") if isinstance(payload.get("max"), (int, float)) else None,
    )


def _fields(payload: Any) -> tuple[RegistryField, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise RegistryError("'config_fields' must be a list.")
    fields = tuple(_field(f) for f in payload)
    keys = [f.key for f in fields]
    if len(keys) != len(set(keys)):
        raise RegistryError(f"Duplicate config field keys: {keys}")
    return fields


def _port(payload: Any) -> PortDefinition:
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        raise RegistryError("Port entries require a non-empty string 'name'.")
    direction = payload.get("direction")
    if direction not in {"input", "output"}:
        raise RegistryError(f"Port {payload['name']!r} has invalid direction {direction!r}.")
    data_types = payload.get("data_types") or ["any"]
    if not isinstance(data_types, list) or not data_types:
        raise RegistryError(f"Port {payload['name']!r} requires at least one data type.")
    return PortDefinition(
        name=payload["name"],
        label=str(payload.get("label") or payload["name"]),
        direction=direction,
        data_types=tuple(str(t) for t in data_types),
        required=bool(payload.get("required")),
        multiple=bool(payload.get("multiple")),
        description=payload.get("description"),
        default_value=payload.get("default_value"),
        dynamic=bool(payload.get("dynamic")),
    )


def _ports(payload: Any) -> tuple[PortDefinition, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise RegistryError("'input_ports'/'output_ports' must be lists.")
    ports = tuple(_port(p) for p in payload)
    names = [p.name for p in ports]
    if len(names) != len(set(names)):
        raise RegistryError(f"Duplicate port names: {names}")
    return ports


def _node(payload: Any) -> NodeDefinition:
    if not isinstance(payload, dict):
        raise RegistryError("Node entries must be objects.")
    node_type = payload.get("type")
    version = payload.get("version")
    if not isinstance(node_type, str) or not node_type:
        raise RegistryError("Node entries require a non-empty string 'type'.")
    if not isinstance(version, int) or version < 1:
        raise RegistryError(f"Node {node_type!r} requires a positive integer 'version'.")
    outputs = payload.get("outputs") or ["out"]
    if not isinstance(outputs, list) or not outputs:
        raise RegistryError(f"Node {node_type!r} requires at least one output port.")
    default_config = payload.get("default_config") or {}
    if not isinstance(default_config, dict):
        raise RegistryError(f"Node {node_type!r} 'default_config' must be an object.")
    has_input = bool(payload.get("has_input", True))
    input_ports = _ports(payload.get("input_ports"))
    if has_input and not input_ports:
        raise RegistryError(f"Node {node_type!r} has an input but no input ports.")
    output_ports = _ports(payload.get("output_ports"))
    if not output_ports:
        raise RegistryError(f"Node {node_type!r} requires at least one output port.")
    return NodeDefinition(
        type=node_type,
        version=version,
        label=str(payload.get("label") or node_type),
        description=str(payload.get("description") or ""),
        category=str(payload.get("category") or "Utilities"),
        source=str(payload.get("source") or "builtin"),
        outputs=tuple(str(o) for o in outputs),
        has_input=has_input,
        input_ports=input_ports,
        output_ports=output_ports,
        fan_in=bool(payload.get("fan_in")),
        terminal=bool(payload.get("terminal")),
        capabilities=tuple(str(c) for c in (payload.get("capabilities") or [])),
        documentation_url=payload.get("documentation_url"),
        default_config=dict(default_config),
        config_fields=_fields(payload.get("config_fields")),
    )


def _trigger(payload: Any) -> TriggerDefinition:
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        raise RegistryError("Trigger entries require a string 'type'.")
    version = payload.get("version")
    if not isinstance(version, int) or version < 1:
        raise RegistryError(f"Trigger {payload.get('type')!r} requires a positive integer 'version'.")
    output_ports = _ports(payload.get("output_ports"))
    if not output_ports:
        output_ports = (PortDefinition(
            name="out", label="Output", direction="output",
            data_types=("items",), multiple=True,
        ),)
    return TriggerDefinition(
        type=payload["type"],
        version=version,
        label=str(payload.get("label") or payload["type"]),
        description=str(payload.get("description") or ""),
        config_fields=_fields(payload.get("config_fields")),
        output_ports=output_ports,
    )


@lru_cache(maxsize=1)
def load_registry() -> NodeRegistry:
    """Load and structurally validate the generated registry exactly once."""
    if not REGISTRY_PATH.is_file():
        raise RegistryError(
            f"Node registry missing at {REGISTRY_PATH}. Run the web registry test "
            "with WRITE_NODE_REGISTRY=1."
        )
    try:
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"Node registry is not readable JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryError("Node registry must be a JSON object.")

    nodes: dict[tuple[str, int], NodeDefinition] = {}
    latest: dict[str, int] = {}
    for payload in raw.get("nodes") or []:
        node = _node(payload)
        key = (node.type, node.version)
        if key in nodes:
            raise RegistryError(f"Duplicate node definition {node.type}@{node.version}.")
        nodes[key] = node
        latest[node.type] = max(node.version, latest.get(node.type, 0))
    triggers: dict[str, TriggerDefinition] = {}
    for payload in raw.get("triggers") or []:
        trigger = _trigger(payload)
        if trigger.type in triggers:
            raise RegistryError(f"Duplicate trigger definition {trigger.type}.")
        triggers[trigger.type] = trigger

    if not nodes:
        raise RegistryError("Node registry has no node definitions.")

    return NodeRegistry(
        registry_version=str(raw.get("registry_version") or "1.0"),
        generated_from=str(raw.get("generated_from") or ""),
        nodes=nodes,
        latest=latest,
        triggers=triggers,
        raw=raw,
    )


def node_types() -> frozenset[str]:
    return frozenset(load_registry().latest)


def trigger_types() -> frozenset[str]:
    return frozenset(load_registry().triggers)


def latest_node_version(node_type: str) -> Optional[int]:
    return load_registry().latest.get(node_type)


def node_definition(node_type: str, version: Optional[int] = None) -> Optional[NodeDefinition]:
    return load_registry().node(node_type, version)


def registry_version() -> str:
    return load_registry().registry_version


PORT_ACCEPTS: dict[str, frozenset[str]] = {
    "any": frozenset({"any", "items", "json", "text", "number", "boolean", "binary"}),
    "items": frozenset({"items", "json", "text", "number", "boolean", "any"}),
    "json": frozenset({"json", "any"}),
    "text": frozenset({"text", "any"}),
    "number": frozenset({"number", "any"}),
    "boolean": frozenset({"boolean", "any"}),
    "binary": frozenset({"binary", "any"}),
}


def are_port_types_compatible(source_types: Iterable[str], target_types: Iterable[str]) -> bool:
    """True when at least one source type is accepted by the target port."""
    sources = list(source_types)
    targets = list(target_types)
    if not sources or not targets:
        return True
    return any(
        target in PORT_ACCEPTS.get(source, frozenset())
        for source in sources
        for target in targets
    )


def input_ports(node_type: str, version: Optional[int] = None) -> list[PortDefinition]:
    definition = node_definition(node_type, version)
    return list(definition.input_ports) if definition else []


def _switch_route_names(config: Optional[dict[str, Any]]) -> list[str]:
    routes = (config or {}).get("routes") if isinstance(config, dict) else None
    if not isinstance(routes, list):
        return []
    return [
        r["name"] for r in routes
        if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"]
    ]


def output_ports(
    node_type: str,
    config: Optional[dict[str, Any]] = None,
    version: Optional[int] = None,
) -> list[PortDefinition]:
    """Output ports for a node instance; switch routes become named ports."""
    definition = node_definition(node_type, version)
    if definition is None:
        return []
    if node_type == "switch":
        routes = _switch_route_names(config)
        names = [*routes, "default"] if routes else ["out"]
        ports = [
            PortDefinition(
                name=name,
                label="Default" if name == "default" else name,
                direction="output",
                data_types=("items",),
                multiple=True,
                dynamic=True,
            )
            for name in names
        ]
    else:
        ports = list(definition.output_ports)
    # n8n's "On Error → Continue (using error output)" adds a second output.
    raw_mode = (config or {}).get("on_error") if isinstance(config, dict) else None
    mode = str(raw_mode or "").lower().replace("_", "").replace("-", "").replace(" ", "")
    if mode in ("continueerroroutput", "continueonerroroutput") and not any(
        port.name == "error" for port in ports
    ):
        ports.append(PortDefinition(
            name="error",
            label="Error",
            direction="output",
            data_types=("items",),
            multiple=False,
            dynamic=True,
        ))
    return ports


def trigger_output_ports(trigger_type: str) -> list[PortDefinition]:
    trigger = load_registry().triggers.get(trigger_type)
    return list(trigger.output_ports) if trigger else [
        PortDefinition(name="out", label="Output", direction="output",
                       data_types=("items",), multiple=True)
    ]


def default_input_port(ports: Iterable[PortDefinition]) -> Optional[PortDefinition]:
    entries = list(ports)
    for port in entries:
        if port.required:
            return port
    return entries[0] if entries else None


def find_port(ports: Iterable[PortDefinition], name: str) -> Optional[PortDefinition]:
    return next((p for p in ports if p.name == name), None)


def registry_document() -> dict[str, Any]:
    """Defensive copy for API responses."""
    return json.loads(json.dumps(load_registry().raw))


def check_node_version(node_type: str, type_version: Optional[int]) -> tuple[Optional[str], Optional[str]]:
    """Return (error, warning) for a pinned node version, mirroring the web check."""
    if type_version is None:
        return None, None
    registry = load_registry()
    latest = registry.latest.get(node_type)
    if latest is None:
        return None, None
    if type_version > latest:
        return (
            f"Node type {node_type!r} version {type_version} is newer than the registry "
            f"(latest {latest}).",
            None,
        )
    if type_version < latest:
        return (
            None,
            f"Node type {node_type!r} is pinned to v{type_version}; v{latest} is available.",
        )
    return None, None


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def validate_node_parameters(
    node_type: str,
    config: dict[str, Any],
    type_version: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Validate a node config against its registry definition.

    This is the parameter contract used by the AI planner and by
    ``POST /nodes/{type}/validate``. It intentionally covers structural rules
    (required, type, select options, numeric bounds); deeper per-node rules stay
    in the workflow definition validator so both layers agree.
    """
    issues: list[dict[str, Any]] = []
    definition = node_definition(node_type, type_version)
    if definition is None:
        return [{
            "code": "UNKNOWN_NODE_TYPE",
            "message": f"Unknown node type {node_type!r}.",
            "severity": "error",
            "field": "type",
        }]
    if not isinstance(config, dict):
        return [{
            "code": "INVALID_NODE_CONFIG",
            "message": "Node config must be an object.",
            "severity": "error",
            "field": "config",
        }]

    for field in definition.config_fields:
        value = config.get(field.key)
        if field.required and _is_empty(value):
            issues.append({
                "code": "NODE_CONFIG_REQUIRED",
                "message": f"Node {node_type!r} requires 'config.{field.key}'.",
                "severity": "error",
                "field": f"config.{field.key}",
            })
            continue
        if _is_empty(value):
            continue
        if field.type == "select" and field.options:
            allowed = {o["value"] for o in field.options}
            if str(value) not in allowed:
                issues.append({
                    "code": "NODE_CONFIG_INVALID_OPTION",
                    "message": f"Node {node_type!r} 'config.{field.key}' must be one of {sorted(allowed)}.",
                    "severity": "error",
                    "field": f"config.{field.key}",
                })
        elif field.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append({
                    "code": "NODE_CONFIG_TYPE",
                    "message": f"Node {node_type!r} 'config.{field.key}' must be a number.",
                    "severity": "error",
                    "field": f"config.{field.key}",
                })
            elif field.min is not None and value < field.min:
                issues.append({
                    "code": "NODE_CONFIG_RANGE",
                    "message": f"Node {node_type!r} 'config.{field.key}' must be >= {field.min}.",
                    "severity": "error",
                    "field": f"config.{field.key}",
                })
            elif field.max is not None and value > field.max:
                issues.append({
                    "code": "NODE_CONFIG_RANGE",
                    "message": f"Node {node_type!r} 'config.{field.key}' must be <= {field.max}.",
                    "severity": "error",
                    "field": f"config.{field.key}",
                })
        elif field.type == "boolean" and not isinstance(value, bool):
            issues.append({
                "code": "NODE_CONFIG_TYPE",
                "message": f"Node {node_type!r} 'config.{field.key}' must be a boolean.",
                "severity": "error",
                "field": f"config.{field.key}",
            })
        elif field.type == "stringlist" and not isinstance(value, list):
            issues.append({
                "code": "NODE_CONFIG_TYPE",
                "message": f"Node {node_type!r} 'config.{field.key}' must be a list.",
                "severity": "error",
                "field": f"config.{field.key}",
            })

    for key in definition.default_config:
        if key not in {f.key for f in definition.config_fields}:
            issues.append({
                "code": "REGISTRY_DEFAULT_WITHOUT_FIELD",
                "message": f"Registry default {key!r} has no declared field.",
                "severity": "warning",
                "field": f"config.{key}",
            })
    return issues


def prompt_catalog(max_chars: int = 3000) -> str:
    """Compact, deterministic catalog for Koro's system prompt."""
    registry = load_registry()
    by_category: dict[str, list[str]] = {}
    for node in sorted(registry.nodes.values(), key=lambda n: (n.category, n.type)):
        by_category.setdefault(node.category, []).append(f"{node.type}@{node.version}")
    lines = [
        f"Node registry v{registry.registry_version} "
        f"({len(registry.latest)} node types, {len(registry.triggers)} triggers)."
    ]
    for category, entries in by_category.items():
        lines.append(f"{category}: {', '.join(entries)}")
    text = "\n".join(lines)
    return text[:max_chars]


def registry_summary() -> dict[str, Any]:
    registry = load_registry()
    return {
        "registry_version": registry.registry_version,
        "node_count": len(registry.nodes),
        "node_type_count": len(registry.latest),
        "trigger_count": len(registry.triggers),
        "generated_from": registry.generated_from,
    }


def iter_definitions() -> Iterable[NodeDefinition]:
    return load_registry().nodes.values()
