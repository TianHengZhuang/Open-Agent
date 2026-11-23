"""Connection/port contract tests for workflow definitions (no DB)."""

from copy import deepcopy

import pytest

from openagent.registry import node_registry
from openagent.registry.node_registry import PortDefinition
from openagent.services import workflow_definition
from openagent.services.workflow_definition import validate_definition


def _definition(edges=None):
    return {
        "schema_version": "1.1",
        "triggers": [
            {"id": "trg", "type": "manual", "name": "Start", "config": {}},
        ],
        "nodes": [
            {"id": "n_transform", "type": "transform", "name": "Map",
             "config": {"mapping": {"ok": True}}},
            {"id": "n_set", "type": "set", "name": "Set",
             "config": {"values": {"ok": True}}},
        ],
        "edges": edges if edges is not None else [
            {"id": "e1", "from": "trg", "to": "n_transform"},
            {"id": "e2", "from": "n_transform", "to": "n_set"},
        ],
        "variables": [],
        "settings": {},
    }


def _codes(result):
    return {e.code for e in result.errors}


def _warnings(result):
    return {w.code for w in result.warnings}


def test_legacy_edges_without_ports_still_validate():
    result = validate_definition(_definition())
    assert result.valid is True, result.to_dict()
    assert not any(code.startswith(("UNKNOWN_", "INCOMPATIBLE")) for code in _codes(result))


def test_typed_ports_validate_and_are_optional():
    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform", "from_port": "out", "to_port": "in"},
        {"id": "e2", "from": "n_transform", "to": "n_set",
         "from_port": "out", "to_port": "in", "connection_type": "main"},
    ])
    result = validate_definition(definition)
    assert result.valid is True, result.to_dict()


def test_unknown_source_port_is_reported_with_node_and_edge():
    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform"},
        {"id": "e2", "from": "n_transform", "to": "n_set", "from_port": "missing"},
    ])
    result = validate_definition(definition)
    issue = next(e for e in result.errors if e.code == "UNKNOWN_SOURCE_PORT")
    assert issue.node_id == "n_transform"
    assert issue.edge_id == "e2"
    assert issue.field == "edges[1].from_port"


def test_unknown_target_port_is_reported():
    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform"},
        {"id": "e2", "from": "n_transform", "to": "n_set", "to_port": "missing"},
    ])
    result = validate_definition(definition)
    issue = next(e for e in result.errors if e.code == "UNKNOWN_TARGET_PORT")
    assert issue.node_id == "n_set" and issue.edge_id == "e2"


def test_duplicate_connection_is_rejected_per_ports():
    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform"},
        {"id": "e2", "from": "n_transform", "to": "n_set", "from_port": "out"},
        {"id": "e3", "from": "n_transform", "to": "n_set", "from_port": "out"},
    ])
    result = validate_definition(definition)
    assert "DUPLICATE_EDGE" in _codes(result)


def test_condition_branching_uses_named_outputs():
    definition = _definition()
    definition["nodes"] = [
        {"id": "n_cond", "type": "condition", "name": "If",
         "config": {"expression": "ok == true"}},
        {"id": "n_set", "type": "set", "name": "Set",
         "config": {"values": {"yes": True}}},
        {"id": "n_set2", "type": "set", "name": "Set 2",
         "config": {"values": {"no": True}}},
    ]
    definition["edges"] = [
        {"id": "e0", "from": "trg", "to": "n_cond"},
        {"id": "e1", "from": "n_cond", "to": "n_set", "from_port": "true"},
        {"id": "e2", "from": "n_cond", "to": "n_set2", "from_port": "false"},
    ]
    result = validate_definition(definition)
    assert result.valid is True, result.to_dict()


def test_merge_port_accepts_multiple_inbound_connections():
    definition = _definition()
    definition["nodes"].extend([
        {"id": "n_merge", "type": "merge", "name": "Join", "config": {}},
        {"id": "n_set2", "type": "set", "name": "Set 2",
         "config": {"values": {"ok": True}}},
    ])
    definition["edges"] = [
        {"id": "e1", "from": "trg", "to": "n_set"},
        {"id": "e2", "from": "trg", "to": "n_set2"},
        {"id": "e3", "from": "n_set", "to": "n_merge"},
        {"id": "e4", "from": "n_set2", "to": "n_merge"},
        {"id": "e5", "from": "n_merge", "to": "n_transform"},
    ]
    result = validate_definition(definition)
    assert result.valid is True, result.to_dict()


def test_non_merge_input_rejects_multiple_connections():
    definition = _definition()
    definition["edges"] = [
        {"id": "e1", "from": "trg", "to": "n_transform"},
        {"id": "e2", "from": "trg", "to": "n_set"},
        {"id": "e3", "from": "n_transform", "to": "n_set"},
    ]
    result = validate_definition(definition)
    assert "MULTIPLE_INBOUND_EDGES" in _codes(result)


def test_unsupported_connection_type_and_error_routing():
    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform", "connection_type": "audit"},
        {"id": "e2", "from": "n_transform", "to": "n_set"},
    ])
    result = validate_definition(definition)
    assert "UNSUPPORTED_CONNECTION_TYPE" in _codes(result)

    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform"},
        {"id": "e2", "from": "n_transform", "to": "n_set", "connection_type": "error"},
    ])
    result = validate_definition(definition)
    assert "CONNECTION_TYPE_UNAVAILABLE" in _codes(result)


def test_invalid_port_metadata_is_reported():
    definition = _definition([
        {"id": "e1", "from": "trg", "to": "n_transform"},
        {"id": "e2", "from": "n_transform", "to": "n_set",
         "from_port": "", "metadata": "nope"},
    ])
    result = validate_definition(definition)
    assert "INVALID_EDGE_PORT" in _codes(result)
    assert "INVALID_EDGE_METADATA" in _codes(result)


class _PortOverride:
    """Delegate to the real registry, overriding a few node ports."""

    def __init__(self, inputs=None, outputs=None):
        self.inputs = inputs or {}
        self.outputs = outputs or {}

    def input_ports(self, node_type, version=None):
        return self.inputs.get(node_type, node_registry.input_ports(node_type, version))

    def output_ports(self, node_type, config=None, version=None):
        if node_type in self.outputs:
            return self.outputs[node_type]
        return node_registry.output_ports(node_type, config, version)

    def trigger_output_ports(self, trigger_type):
        return node_registry.trigger_output_ports(trigger_type)

    def are_port_types_compatible(self, source, target):
        return node_registry.are_port_types_compatible(source, target)


def test_incompatible_port_types_are_rejected(monkeypatch):
    fake = _PortOverride(
        inputs={"set": [PortDefinition("in", "Input", "input", ("number",), True, False)]},
        outputs={"transform": [PortDefinition("out", "Output", "output", ("text",), True, True)]},
    )
    monkeypatch.setattr(workflow_definition, "_registry", fake)
    result = validate_definition(_definition())
    assert "INCOMPATIBLE_PORT_TYPES" in _codes(result)
    issue = next(e for e in result.errors if e.code == "INCOMPATIBLE_PORT_TYPES")
    assert issue.node_id == "n_set" and issue.edge_id == "e2"


def test_required_input_must_be_connected_when_another_input_is_used(monkeypatch):
    fake = _PortOverride(inputs={
        "merge": [
            PortDefinition("a", "A", "input", ("items",), True, False),
            PortDefinition("b", "B", "input", ("items",), True, False),
        ],
    })
    monkeypatch.setattr(workflow_definition, "_registry", fake)
    definition = _definition()
    definition["nodes"] = [
        {"id": "n_merge", "type": "merge", "name": "Join", "config": {}},
        {"id": "n_set", "type": "set", "name": "Set", "config": {"values": {"ok": True}}},
    ]
    definition["edges"] = [
        {"id": "e1", "from": "trg", "to": "n_merge", "to_port": "a"},
        {"id": "e2", "from": "n_merge", "to": "n_set"},
    ]
    result = validate_definition(definition)
    issue = next(e for e in result.errors if e.code == "NODE_REQUIRED_INPUT_MISSING")
    assert issue.node_id == "n_merge" and issue.field == "input.b"


def test_legacy_condition_labels_are_still_accepted():
    definition = _definition()
    definition["nodes"] = [
        {"id": "n_cond", "type": "condition", "name": "If",
         "config": {"expression": "ok == true"}},
        {"id": "n_set", "type": "set", "name": "Set", "config": {"values": {"x": 1}}},
        {"id": "n_set2", "type": "set", "name": "Set 2", "config": {"values": {"x": 2}}},
    ]
    definition["edges"] = [
        {"id": "e0", "from": "trg", "to": "n_cond"},
        {"id": "e1", "from": "n_cond", "to": "n_set", "label": "true"},
        {"id": "e2", "from": "n_cond", "to": "n_set2", "label": "false"},
    ]
    result = validate_definition(definition)
    assert result.valid is True, result.to_dict()
    assert "INVALID_EDGE_PORT" not in _warnings(result)
