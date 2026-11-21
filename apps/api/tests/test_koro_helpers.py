"""Koro's model-free helpers: workflow outline and run diagnosis."""

from types import SimpleNamespace
from uuid import uuid4

from openagent.api.v1.koro import (
    _diagnosis_text,
    _failure_hint,
    _workflow_outline,
)
from openagent.db.models import WorkflowExecutionStatus


def test_outline_lists_triggers_and_steps():
    definition = {
        "triggers": [{"id": "trigger", "type": "manual", "name": "Start"}],
        "nodes": [
            {"id": "n1", "type": "set", "name": "Normalize"},
            {"id": "n2", "type": "webhook", "name": "Notify"},
        ],
        "edges": [{"from": "trigger", "to": "n1"}, {"from": "n1", "to": "n2"}],
    }

    summary, steps = _workflow_outline(definition)

    assert "2 step(s)" in summary
    assert "2 connection(s)" in summary
    assert "manual “Start”" in summary
    assert steps == ["1. Normalize [set]", "2. Notify [webhook]"]


def test_failure_hint_matches_common_errors():
    assert "timeout" in _failure_hint({"error": "Request timeout"}).lower()
    assert "credential" in _failure_hint({"error": "403 forbidden"}).lower()
    assert "required configuration" in _failure_hint(
        {"error": "NODE_CONFIG_REQUIRED: missing url"}
    ).lower()


def test_diagnosis_reports_failed_node_and_hint():
    execution = SimpleNamespace(
        id=uuid4(),
        status=WorkflowExecutionStatus.FAILED,
        metadata={
            "summary": {"succeeded": 1, "failed": 1},
            "node_runs": [
                {"node_id": "n1", "node_name": "Normalize", "node_type": "set",
                 "status": "succeeded", "error": None},
                {"node_id": "n2", "node_name": "Notify", "node_type": "webhook",
                 "status": "failed", "error": "connection refused"},
            ],
        },
    )

    detail, data = _diagnosis_text(execution)

    assert "Notify" in detail
    assert "connection refused" in detail
    assert data["status"] == "failed"
    assert data["failed_nodes"][0]["node_name"] == "Notify"
    assert data["failed_nodes"][0]["hint"]


def test_diagnosis_handles_successful_run():
    execution = SimpleNamespace(
        id=uuid4(),
        status=WorkflowExecutionStatus.COMPLETED,
        metadata={"summary": {"succeeded": 3, "duration_ms": 120}, "node_runs": []},
    )

    detail, data = _diagnosis_text(execution)

    assert "completed successfully" in detail
    assert data["status"] == "completed"
