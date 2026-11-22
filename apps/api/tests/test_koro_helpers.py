"""Koro's model-free helpers: workflow outline and run diagnosis."""

from types import SimpleNamespace
from uuid import uuid4

from openagent.api.v1.koro import (
    _append_template_node,
    _command_plan,
    _diagnosis_text,
    _failure_hint,
    _template_definition,
    _workflow_outline,
)
from openagent.db.models import WorkflowExecutionStatus
from openagent.services.workflow_definition import validate_definition


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


def test_offline_template_builds_a_valid_workflow():
    definition = _template_definition(
        "When a new email arrives, summarize it, post urgent ones to Slack and "
        "log the result",
        "qwen2.5:0.5b-instruct",
    )

    verdict = validate_definition(definition)

    assert verdict.valid, verdict.errors
    assert definition["triggers"][0]["type"] == "event"
    types = {node["type"] for node in definition["nodes"]}
    assert "prompt" in types
    assert "webhook" in types
    assert len(definition["edges"]) >= len(definition["nodes"]) - 1


def test_offline_template_uses_schedule_for_daily_requests():
    definition = _template_definition(
        "Every morning, summarize invoices and email me the totals",
        "qwen2.5:0.5b-instruct",
    )

    assert definition["triggers"][0]["type"] == "schedule"
    assert definition["triggers"][0]["config"]["cron"] == "0 9 * * *"
    assert validate_definition(definition).valid


def test_descriptive_create_requests_fall_through_to_the_generator():
    # A bare "create a workflow" still gets the starter, but a description must
    # reach the offline template generator instead.
    assert _command_plan("create a workflow", [], None) is not None
    assert _command_plan("create a workflow that triages support emails", [], None) is None


def test_edit_requests_are_not_mistaken_for_status_checks():
    # "log the result" used to match the inspect branch and steal an edit.
    assert _command_plan("Also log the result to a spreadsheet", [], None) is None
    assert _command_plan("add a Slack notification", [], None) is None

    assert _command_plan("run it", [], None)["action"]["type"] == "run"
    assert _command_plan("show the status", [], None)["action"]["type"] == "inspect"
    assert _command_plan("what happened on the last run", [], None)["action"]["type"] == "inspect"


def test_agent_command_extracts_a_name():
    plan = _command_plan("create an agent called Invoice Summarizer", [], None)

    assert plan is not None
    action = plan["action"]
    assert action["type"] == "create_agent"
    assert action["agent"]["name"] == "Invoice Summarizer"


def test_template_recognizes_third_person_verbs():
    # "summarizes" (not just "summarize") must still create an AI step.
    definition = _template_definition(
        "Build a workflow that summarizes new support emails", "m"
    )

    assert any(node["type"] == "prompt" for node in definition["nodes"])
    assert validate_definition(definition).valid


def test_append_template_node_keeps_the_definition_valid():
    definition = _template_definition("Build a workflow that classifies tickets", "m")
    appended = _append_template_node(definition, "add a Slack notification", "m")

    assert appended is not None
    assert validate_definition(appended).valid
    assert appended["nodes"][-1]["type"] == "webhook"
