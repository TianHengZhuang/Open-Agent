"""Unit tests for the canonical definition model (schema 1.0 -> 1.1)."""

from pydantic import ValidationError

from openagent.services.workflow_definition import (
    SCHEMA_VERSION,
    empty_definition,
    migrate_definition,
    validate_definition,
)
from openagent.services.workflow_schema import (
    CanonicalWorkflowModel,
    DataItemModel,
    ExecutionTraceEventModel,
    PortPayloadModel,
    WorkflowDefinitionModel,
    WorkflowEdgeModel,
    canonical_workflow,
    normalize_definition,
    parse_definition,
)


def _definition(version: str = "1.0") -> dict:
    return {
        "schema_version": version,
        "triggers": [
            {"id": "trg_1", "type": "manual", "name": "Run manually", "config": {}},
        ],
        "nodes": [
            {"id": "n_1", "type": "set", "name": "Set", "config": {"values": {"ok": True}}},
        ],
        "edges": [
            {"id": "e_1", "from": "trg_1", "to": "n_1", "condition": {"when": "always"}},
        ],
        "variables": [],
        "settings": {},
    }


def test_current_schema_version_is_1_1():
    assert SCHEMA_VERSION == "1.1"
    assert empty_definition()["schema_version"] == "1.1"
    assert empty_definition()["static_data"] == {}
    assert empty_definition()["metadata"] == {}


def test_normalize_upgrades_1_0_without_losing_data():
    original = _definition("1.0")
    original["settings"] = {"timezone": "UTC", "custom": {"keep": True}}
    original["future_field"] = {"keep": "me"}

    normalized = normalize_definition(original)

    assert normalized["schema_version"] == "1.1"
    assert normalized["static_data"] == {} and normalized["metadata"] == {}
    assert normalized["settings"]["timezone"] == "UTC"
    assert normalized["settings"]["custom"] == {"keep": True}
    assert normalized["future_field"] == {"keep": "me"}
    # Input is not mutated.
    assert original["schema_version"] == "1.0"
    assert "static_data" not in original


def test_normalize_leaves_unsupported_versions_untouched():
    original = _definition("9.9")
    assert normalize_definition(original) == original


def test_migrate_definition_reports_1_0_upgrade():
    migrated, changed, error = migrate_definition(_definition("1.0"))
    assert error is None and changed is True
    assert migrated["schema_version"] == "1.1"
    assert migrated["nodes"][0]["config"] == {"values": {"ok": True}}

    again, changed_again, error_again = migrate_definition(migrated)
    assert error_again is None and changed_again is False and again == migrated


def test_typed_model_preserves_unknown_keys_and_edge_alias():
    definition = _definition("1.1")
    definition["nodes"][0]["future_node_key"] = 7

    model = parse_definition(normalize_definition(definition))

    assert isinstance(model, WorkflowDefinitionModel)
    assert model.edges[0].from_ == "trg_1"
    dumped = model.model_dump(by_alias=True)
    assert dumped["nodes"][0]["future_node_key"] == 7
    assert dumped["edges"][0]["from"] == "trg_1"


def test_typed_model_rejects_invalid_shapes():
    try:
        parse_definition({"schema_version": "1.1", "nodes": "not-a-list"})
    except ValidationError as exc:
        assert "nodes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Invalid definition should not parse")


def test_canonical_workflow_combines_row_metadata_and_definition():
    workflow = canonical_workflow(
        definition=_definition("1.0"),
        name="Triage",
        description="Support triage",
        tags=["support"],
        active=True,
    )
    assert isinstance(workflow, CanonicalWorkflowModel)
    assert workflow.name == "Triage" and workflow.tags == ["support"] and workflow.active
    assert workflow.definition.schema_version == "1.1"


def test_validator_accepts_1_1_fields():
    definition = _definition("1.1")
    definition["error_workflow_id"] = "11111111-1111-1111-1111-111111111111"
    definition["static_data"] = {"brand": "OpenAgent"}
    definition["metadata"] = {"author": "test"}
    definition["settings"] = {"execution_timeout_seconds": 300, "timezone": "UTC"}

    result = validate_definition(definition)
    assert result.valid is True, result.to_dict()


def test_validator_rejects_malformed_1_1_fields():
    bad_values = [
        ("error_workflow_id", ""),
        ("static_data", []),
        ("metadata", "nope"),
    ]
    for key, value in bad_values:
        definition = _definition("1.1")
        definition[key] = value
        result = validate_definition(definition)
        assert result.valid is False, key
        assert any(e.field == key for e in result.errors), key

    definition = _definition("1.1")
    definition["settings"] = {"execution_timeout_seconds": 0}
    result = validate_definition(definition)
    assert result.valid is False
    assert any(e.field == "settings.execution_timeout_seconds" for e in result.errors)


def test_node_version_compatibility_is_enforced():
    definition = _definition("1.1")
    definition["nodes"][0]["type_version"] = 1
    assert validate_definition(definition).valid is True

    definition["nodes"][0]["type_version"] = 99
    result = validate_definition(definition)
    assert result.valid is False
    assert any(e.code == "NODE_VERSION_UNSUPPORTED" for e in result.errors)


def test_validator_still_accepts_legacy_1_0():
    result = validate_definition(_definition("1.0"))
    assert result.valid is True


def test_edge_model_keeps_typed_ports_and_metadata():
    edge = WorkflowEdgeModel.model_validate({
        "id": "e1",
        "from": "n_1",
        "to": "n_2",
        "from_port": "true",
        "to_port": "in",
        "connection_type": "main",
        "metadata": {"ui": "branch"},
    })
    assert edge.from_ == "n_1" and edge.from_port == "true" and edge.to_port == "in"
    assert edge.metadata == {"ui": "branch"}
    dumped = edge.model_dump(by_alias=True)
    assert dumped["from"] == "n_1"


def test_data_flow_contract_models():
    item = DataItemModel.model_validate({
        "json": {"id": 1},
        "binary": {"file": {
            "mime_type": "application/pdf", "data_ref": "blob://abc", "size_bytes": 12,
        }},
        "paired_item": [0, 1],
        "error": {"message": "retry", "retriable": True},
    })
    payload = PortPayloadModel(port="true", connection_type="main", items=[item])
    assert payload.items[0].binary["file"].mime_type == "application/pdf"
    assert payload.items[0].paired_item == [0, 1]

    trace = ExecutionTraceEventModel.model_validate({
        "execution_id": "exec_1",
        "node_id": "n_1",
        "port": "true",
        "item_index": 0,
        "attempt": 1,
        "status": "succeeded",
        "duration_ms": 5,
        "ts": "2026-01-01T00:00:00Z",
    })
    assert trace.connection_type == "main"

    try:
        ExecutionTraceEventModel.model_validate({
            "execution_id": "e", "node_id": "n", "status": "exploded",
            "ts": "2026-01-01T00:00:00Z",
        })
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Unknown trace status must be rejected")
