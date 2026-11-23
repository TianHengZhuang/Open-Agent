"""Unit tests for the generated canonical node registry (no DB)."""

import pytest

from openagent.registry import node_registry
from openagent.services.workflow_definition import _FALLBACK_NODE_TYPES, _FALLBACK_TRIGGER_TYPES


def test_registry_loads_and_matches_the_authoring_catalog_size():
    registry = node_registry.load_registry()
    assert registry.registry_version
    assert len(registry.nodes) == 42
    assert len(registry.latest) == 42
    assert len(registry.triggers) == 5
    assert node_registry.registry_document()["generated_from"].endswith("node-catalog.ts")


def test_every_backend_fallback_type_is_registered():
    # Existing backend types may never disappear from the shared registry;
    # browser nodes were added to the catalog so both sides match exactly.
    assert _FALLBACK_NODE_TYPES <= node_registry.node_types()
    assert _FALLBACK_TRIGGER_TYPES <= node_registry.trigger_types()
    assert node_registry.latest_node_version("browser_agent") == 1


def test_registry_entries_are_structurally_valid():
    registry = node_registry.load_registry()
    for definition in registry.nodes.values():
        assert definition.version >= 1
        assert definition.outputs
        keys = [f.key for f in definition.config_fields]
        assert len(keys) == len(set(keys))
        assert set(definition.default_config) <= set(keys)
        assert definition.output_ports
        if definition.has_input:
            assert definition.input_ports
        for port in (*definition.input_ports, *definition.output_ports):
            assert port.direction in {"input", "output"}
            assert port.data_types


def test_condition_and_switch_ports_are_named():
    condition = node_registry.node_definition("condition")
    assert [p.name for p in condition.output_ports] == ["true", "false"]

    switch = node_registry.node_definition("switch")
    static = [p.name for p in switch.output_ports]
    assert static == ["out"]
    dynamic = node_registry.output_ports(
        "switch", {"routes": [{"name": "vip", "expression": "tier == 'vip'"}]}
    )
    assert [p.name for p in dynamic] == ["vip", "default"]
    assert all(p.dynamic for p in dynamic)


def test_port_type_compatibility_matrix():
    assert node_registry.are_port_types_compatible(["items"], ["json"])
    assert node_registry.are_port_types_compatible(["any"], ["binary"])
    assert not node_registry.are_port_types_compatible(["text"], ["number"])
    assert not node_registry.are_port_types_compatible(["binary"], ["items"])
    assert node_registry.are_port_types_compatible([], ["number"])


def test_node_versions_resolve_and_reject_future_versions():
    definition = node_registry.node_definition("condition")
    assert definition is not None and definition.version == 1
    assert node_registry.node_definition("condition", 1) is definition
    assert node_registry.node_definition("condition", 2) is None

    error, warning = node_registry.check_node_version("condition", 2)
    assert error and "newer than the registry" in error
    assert warning is None
    error, warning = node_registry.check_node_version("condition", 1)
    assert error is None and warning is None
    # Unknown types are reported by the definition validator, not here.
    assert node_registry.check_node_version("teleport", 99) == (None, None)


def test_parameter_validation_required_select_and_ranges():
    issues = node_registry.validate_node_parameters("prompt", {"prompt": "Hello"})
    assert any(i["code"] == "NODE_CONFIG_REQUIRED" and i["field"] == "config.model" for i in issues)

    issues = node_registry.validate_node_parameters(
        "browser_action", {"action_type": "TELEPORT"}
    )
    assert any(i["code"] == "NODE_CONFIG_INVALID_OPTION" for i in issues)

    issues = node_registry.validate_node_parameters("delay", {"duration_seconds": 0})
    assert any(i["code"] == "NODE_CONFIG_RANGE" for i in issues)

    issues = node_registry.validate_node_parameters("delay", {"duration_seconds": "soon"})
    assert any(i["code"] == "NODE_CONFIG_TYPE" for i in issues)

    assert node_registry.validate_node_parameters(
        "delay", {"duration_seconds": 30}
    ) == []


def test_unknown_node_and_invalid_config_are_reported():
    issues = node_registry.validate_node_parameters("teleport", {})
    assert issues[0]["code"] == "UNKNOWN_NODE_TYPE"

    issues = node_registry.validate_node_parameters("delay", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert issues[0]["code"] == "INVALID_NODE_CONFIG"


def test_prompt_catalog_is_bounded_and_contains_the_shared_surface():
    catalog = node_registry.prompt_catalog(max_chars=500)
    assert len(catalog) <= 500
    assert "Node registry" in catalog

    full = node_registry.prompt_catalog()
    assert "browser_agent@1" in full
    assert "condition@1" in full
    assert "Triggers" not in full or "manual" in full


def test_koro_receives_the_registry_catalog():
    from openagent.api.v1.koro import _SYSTEM_PROMPT

    assert "Node registry" in _SYSTEM_PROMPT
    assert "browser_agent@1" in _SYSTEM_PROMPT


def test_registry_document_is_a_defensive_copy():
    first = node_registry.registry_document()
    first["nodes"].clear()
    assert node_registry.registry_document()["nodes"]


@pytest.mark.parametrize("node_type", ["set", "transform", "condition", "switch", "merge"])
def test_core_engine_nodes_have_registry_defaults(node_type):
    definition = node_registry.node_definition(node_type)
    assert definition is not None
    assert isinstance(definition.default_config, dict)
