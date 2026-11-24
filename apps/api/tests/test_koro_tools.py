"""Chat control plane: deterministic plans, permissions and real side effects.

The assistant is the product surface, so every workflow/run/agent operation it
claims to support is exercised here: planning, permission checks, versioning,
and the actual database mutation.
"""

from __future__ import annotations

import types
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from openagent.api.v1 import koro as koro_module
from openagent.api.v1.koro import (
    KoroChatRequest,
    _command_plan,
    _cron_from_text,
    _mutate_definition,
)
from openagent.db.models import (
    Agent,
    AgentType,
    Credential,
    CredentialType,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowStatus,
    WorkflowVersion,
)
from openagent.services.workflow_definition import validate_definition


def _definition(nodes=None, triggers=None, edges=None, variables=None):
    return {
        "schema_version": "1.1",
        "triggers": triggers or [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes
        if nodes is not None
        else [{"id": "n1", "type": "set", "name": "Set values", "config": {"values": {"ok": True}}}],
        "edges": edges if edges is not None else [{"id": "e1", "from": "t", "to": "n1"}],
        "variables": variables or [],
        "settings": {},
    }


async def _workflow(db, org, name="Lead intake", definition=None):
    workflow = Workflow(
        organization_id=org.id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        status=WorkflowStatus.ACTIVE,
    )
    db.add(workflow)
    await db.flush()
    db.add(
        WorkflowVersion(
            workflow_id=workflow.id,
            version="v1",
            schema_version="1.1",
            definition=definition or _definition(),
        )
    )
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def _chat(db, org, user, message, **kwargs):
    request = Request(
        {"type": "http", "method": "POST", "path": "/copilot/chat", "headers": []}
    )
    return await koro_module.koro_chat(
        request,
        org.id,
        KoroChatRequest(message=message, **kwargs),
        auth_context=types.SimpleNamespace(user_id=user.id),
        db=db,
    )


@pytest.fixture
def allow_permissions(monkeypatch):
    async def allow(*args, **kwargs):
        return None

    monkeypatch.setattr(koro_module, "require_permission", lambda permission: allow)


@pytest.fixture
def deny_delete(monkeypatch):
    async def allow(*args, **kwargs):
        return None

    async def deny(*args, **kwargs):
        raise HTTPException(
            status_code=403,
            detail={"error": "Forbidden", "code": "FORBIDDEN"},
        )

    monkeypatch.setattr(
        koro_module,
        "require_permission",
        lambda permission: deny if permission == "workflow:delete" else allow,
    )


# ---------------------------------------------------------------------------
# Deterministic planning
# ---------------------------------------------------------------------------


def test_plan_routes_lifecycle_requests():
    workflow = types.SimpleNamespace(
        id=uuid4(), name="Lead intake", slug="lead-intake",
        status=WorkflowStatus.ACTIVE,
    )
    plans = {
        "delete the Lead intake workflow": "delete",
        "duplicate the Lead intake workflow": "duplicate",
        "show the versions of Lead intake": "versions",
        "restore version 3 of Lead intake": "restore",
        "show the last 5 runs of Lead intake": "runs",
        "retry the Lead intake workflow": "retry",
        "cancel the waiting run of Lead intake": "cancel",
        "list agents": "list_agents",
        "show credentials": "list_credentials",
        "show variables": "variables",
        "declare a variable called ticket_id as string": "set_variable",
    }

    for message, expected in plans.items():
        plan = _command_plan(message, [workflow], None)
        assert plan is not None, message
        assert plan["action"]["type"] == expected, message


def test_plan_restore_reads_the_version_number():
    workflow = types.SimpleNamespace(
        id=uuid4(), name="Lead intake", slug="lead-intake", status=WorkflowStatus.ACTIVE,
    )
    plan = _command_plan("roll back Lead intake to v7", [workflow], None)

    assert plan["action"] == {
        "type": "restore", "workflow": "Lead intake", "version": "v7",
    }


def test_plan_batches_publish_then_run():
    workflow = types.SimpleNamespace(
        id=uuid4(), name="Lead intake", slug="lead-intake", status=WorkflowStatus.ACTIVE,
    )
    plan = _command_plan("publish and run Lead intake", [workflow], None)

    action = plan["action"]
    assert action["type"] == "batch"
    assert [item["type"] for item in action["actions"]] == ["publish", "run"]


def test_plan_node_tools():
    workflow = types.SimpleNamespace(
        id=uuid4(), name="Lead intake", slug="lead-intake", status=WorkflowStatus.ACTIVE,
    )

    added = _command_plan("add a Slack notification to Lead intake", [workflow], None)
    assert added["action"]["type"] == "add_node"
    assert added["action"]["node"]["type"] == "webhook"

    connected = _command_plan("connect Start to Lead intake", [workflow], None)
    # "Start" is not a node here; planning still emits the typed tool so the
    # executor can report a precise, recoverable error.
    assert connected["action"]["type"] == "connect"

    removed = _command_plan(
        'remove the "Send notification" node from Lead intake', [workflow], None
    )
    assert removed["action"]["type"] == "remove_node"
    assert removed["action"]["node"] == "Send notification"


def test_plan_schedule_parses_human_cron():
    assert _cron_from_text("set the schedule to every morning at 9") == "0 9 * * *"
    assert _cron_from_text("run every 15 minutes") == "*/15 * * * *"
    assert _cron_from_text("change it to hourly") == "0 * * * *"
    assert _cron_from_text("every week at 6pm") == "0 18 * * 1"


def test_plan_without_target_does_not_invent_node_edits():
    # Editing nodes needs a workflow; the parser must not invent one.
    assert _command_plan("add a Slack notification", [], None) is None


def test_plan_control_plane_tools():
    workflow = types.SimpleNamespace(
        id=uuid4(), name="Lead intake", slug="lead-intake", status=WorkflowStatus.ACTIVE,
    )

    tested = _command_plan('test the "Send notification" node in Lead intake', [workflow], None)
    assert tested["action"]["type"] == "test_node"
    assert tested["action"]["node"] == "Send notification"

    renamed = _command_plan('rename Lead intake to "Intake v2"', [workflow], None)
    assert renamed["action"]["type"] == "update_workflow"
    assert renamed["action"]["name"] == "Intake v2"

    concurrency = _command_plan("set concurrency to 2 for Lead intake", [workflow], None)
    assert concurrency["action"]["type"] == "set_workflow_setting"
    assert concurrency["action"]["max_concurrency"] == 2

    timeout = _command_plan("set a timeout of 5 minutes for Lead intake", [workflow], None)
    assert timeout["action"]["execution_timeout_seconds"] == 300

    failure = _command_plan("when this fails run Failure handler", [workflow], None)
    assert failure["action"]["type"] == "set_workflow_setting"
    assert failure["action"]["error_workflow"] == "Failure handler"

    retried = _command_plan("retry the Send notification node 4 times", [workflow], None)
    assert retried["action"]["type"] == "set_node_settings"
    assert retried["action"]["retry_on_fail"] is True
    assert retried["action"]["max_tries"] == 4

    routed = _command_plan(
        'set on error to continue with error output for the "Send notification" node',
        [workflow],
        None,
    )
    assert routed["action"]["type"] == "set_node_settings"
    assert routed["action"]["on_error"] == "continueErrorOutput"


def test_mutate_node_settings_from_chat():
    definition = _definition()

    updated, error = _mutate_definition(
        definition,
        {
            "type": "set_node_settings",
            "node": "Set values",
            "on_error": "continueErrorOutput",
            "retry_on_fail": True,
            "max_tries": 4,
            "wait_between_tries_ms": 250,
            "execute_once": True,
            "always_output_data": True,
        },
    )

    assert error is None
    node = updated["nodes"][0]
    assert node["on_error"] == "continueErrorOutput"
    assert node["retry_on_fail"] is True
    assert node["max_tries"] == 4
    assert node["wait_between_tries_ms"] == 250
    assert node["execute_once"] is True
    assert node["always_output_data"] is True
    assert validate_definition(updated).valid

    rejected, message = _mutate_definition(
        definition, {"type": "set_node_settings", "node": "Set values", "max_tries": 11}
    )
    assert rejected is None
    assert "1..10" in message

    reset, error = _mutate_definition(
        updated, {"type": "set_node_settings", "node": "Set values", "on_error": "stop"}
    )
    assert error is None
    assert "on_error" not in reset["nodes"][0]


def test_mutate_link_subworkflow_sets_the_target():
    child_id = str(uuid4())
    definition = _definition(
        nodes=[
            {
                "id": "n1",
                "type": "subworkflow",
                "name": "Run child",
                "config": {"workflow_id": str(uuid4())},
            }
        ]
    )

    updated, error = _mutate_definition(
        definition, {"type": "link_subworkflow", "sub_workflow_id": child_id}
    )

    assert error is None
    assert updated["nodes"][0]["config"]["workflow_id"] == child_id


def test_mutate_link_subworkflow_needs_a_node():
    updated, error = _mutate_definition(
        _definition(), {"type": "link_subworkflow", "sub_workflow_id": str(uuid4())}
    )

    assert updated is None
    assert "Subworkflow node" in (error or "")


def test_mutate_workflow_settings():
    definition = _definition()

    updated, error = _mutate_definition(
        definition,
        {
            "type": "set_workflow_setting",
            "max_concurrency": 2,
            "execution_timeout_seconds": 300,
        },
    )
    assert error is None
    assert updated["settings"]["max_concurrency"] == 2
    assert updated["settings"]["execution_timeout_seconds"] == 300
    assert validate_definition(updated).valid

    cleared, error = _mutate_definition(
        updated, {"type": "set_workflow_setting", "max_concurrency": 0}
    )
    assert error is None
    assert "max_concurrency" not in cleared["settings"]

    rejected, message = _mutate_definition(
        definition, {"type": "set_workflow_setting", "max_concurrency": 64}
    )
    assert rejected is None
    assert "1..32" in message


# ---------------------------------------------------------------------------
# Definition mutation helpers
# ---------------------------------------------------------------------------


def test_mutate_add_node_connects_to_the_end_of_the_graph():
    definition = _definition()

    updated, error = _mutate_definition(
        definition,
        {
            "type": "add_node",
            "node": {"type": "delay", "name": "Wait", "config": {"duration_seconds": 5}},
        },
    )

    assert error is None
    assert [node["name"] for node in updated["nodes"]] == ["Set values", "Wait"]
    assert {"from": "n1", "to": "n2"}.items() <= updated["edges"][-1].items()
    assert validate_definition(updated).valid


def test_mutate_connect_disconnect_and_remove():
    definition = _definition(
        nodes=[
            {"id": "n1", "type": "set", "name": "One", "config": {"values": {}}},
            {"id": "n2", "type": "set", "name": "Two", "config": {"values": {}}},
        ],
        edges=[{"id": "e1", "from": "t", "to": "n1"}],
    )

    connected, error = _mutate_definition(
        definition, {"type": "connect", "from": "One", "to": "Two"},
    )
    assert error is None
    assert any(edge["from"] == "n1" and edge["to"] == "n2" for edge in connected["edges"])

    duplicate, message = _mutate_definition(
        connected, {"type": "connect", "from": "One", "to": "Two"},
    )
    assert duplicate is None
    assert "already connected" in message

    disconnected, error = _mutate_definition(
        connected, {"type": "disconnect", "from": "One", "to": "Two"},
    )
    assert error is None
    assert not any(
        edge["from"] == "n1" and edge["to"] == "n2" for edge in disconnected["edges"]
    )

    removed, error = _mutate_definition(
        connected, {"type": "remove_node", "node": "Two"},
    )
    assert error is None
    assert [node["id"] for node in removed["nodes"]] == ["n1"]
    assert all("n2" not in (edge["from"], edge["to"]) for edge in removed["edges"])


def test_mutate_update_node_merges_config():
    definition = _definition()

    updated, error = _mutate_definition(
        definition,
        {
            "type": "update_node",
            "node": "Set values",
            "node_config": {"values": {"ok": False}},
            "config": {"values": {"ok": False, "extra": 1}},
            "name": "Normalise record",
        },
    )

    assert error is None
    node = updated["nodes"][0]
    assert node["name"] == "Normalise record"
    assert node["config"]["values"] == {"ok": False, "extra": 1}


def test_mutate_set_variable_and_schedule():
    definition = _definition()

    updated, error = _mutate_definition(
        definition,
        {"type": "set_variable", "variables": {"ticket_id": {"type": "string", "required": True}}},
    )
    assert error is None
    assert updated["variables"] == [
        {"name": "ticket_id", "type": "string", "required": True}
    ]

    bad, message = _mutate_definition(
        definition, {"type": "set_variable", "variables": {"not a name": "string"}},
    )
    assert bad is None
    assert "not a valid variable" in message

    scheduled = _definition(
        triggers=[
            {"id": "t", "type": "schedule", "name": "Every morning", "config": {"cron": "0 8 * * *"}}
        ],
        edges=[],
    )
    changed, error = _mutate_definition(
        scheduled, {"type": "set_schedule", "cron": "0 9 * * *"},
    )
    assert error is None
    assert changed["triggers"][0]["config"]["cron"] == "0 9 * * *"


# ---------------------------------------------------------------------------
# Chat side effects
# ---------------------------------------------------------------------------


async def test_chat_duplicates_a_workflow(db_session, org_a, user_a, allow_permissions):
    await _workflow(db_session, org_a)

    result = await _chat(db_session, org_a, user_a, "duplicate the Lead intake workflow")

    assert result.actions[0].type == "duplicate"
    assert result.actions[0].status == "ok"
    rows = (
        await db_session.execute(
            select(Workflow).where(Workflow.organization_id == org_a.id)
        )
    ).scalars().all()
    assert {row.name for row in rows} == {"Lead intake", "Lead intake (copy)"}
    copy = next(row for row in rows if row.name.endswith("(copy)"))
    assert copy.status == WorkflowStatus.DRAFT
    version = (
        await db_session.execute(
            select(WorkflowVersion).where(WorkflowVersion.workflow_id == copy.id)
        )
    ).scalar_one()
    assert version.definition["nodes"][0]["id"] == "n1"


async def test_chat_restores_a_version(db_session, org_a, user_a, allow_permissions):
    workflow = await _workflow(db_session, org_a)

    asked = await _chat(db_session, org_a, user_a, "restore v1 of Lead intake")
    assert asked.actions[0].type == "restore"
    assert asked.actions[0].status == "confirmation_required"

    result = await _chat(
        db_session, org_a, user_a,
        f"confirm {asked.actions[0].data['confirmation_token']}",
    )
    assert result.actions[0].type == "restore"
    assert result.actions[0].status == "ok"
    assert result.actions[0].data["restored_from"] == "v1"
    versions = (
        await db_session.execute(
            select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
        )
    ).scalars().all()
    assert sorted(version.version for version in versions) == ["v1", "v2"]


async def test_chat_lists_runs_and_cancels_a_waiting_execution(
    db_session, org_a, user_a, allow_permissions,
):
    workflow = await _workflow(db_session, org_a)
    execution = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.WAITING,
    )
    db_session.add(execution)
    await db_session.commit()

    listed = await _chat(db_session, org_a, user_a, "show the last 5 runs of Lead intake")
    assert listed.actions[0].type == "runs"
    assert listed.actions[0].data["runs"][0]["id"] == str(execution.id)

    cancelled = await _chat(db_session, org_a, user_a, "cancel the waiting run of Lead intake")
    assert cancelled.actions[0].type == "cancel"
    assert cancelled.actions[0].status == "ok"
    await db_session.refresh(execution)
    assert execution.status == WorkflowExecutionStatus.CANCELLED
    assert execution.completed_at is not None


async def test_chat_adds_a_node_to_the_named_workflow(
    db_session, org_a, user_a, allow_permissions,
):
    workflow = await _workflow(db_session, org_a)

    result = await _chat(
        db_session, org_a, user_a, "add a Slack notification to Lead intake",
    )

    assert result.actions[0].type == "edit"
    assert result.actions[0].status == "ok"
    assert result.actions[0].workflow_id == str(workflow.id)
    saved = (
        await db_session.execute(
            select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
        )
    ).scalars().all()
    assert sorted(version.version for version in saved) == ["v1", "v2"]
    edited = next(version for version in saved if version.version == "v2")
    node_types = [node["type"] for node in edited.definition["nodes"]]
    assert node_types == ["set", "webhook"]


async def test_chat_declares_a_workflow_variable(
    db_session, org_a, user_a, allow_permissions,
):
    workflow = await _workflow(db_session, org_a)

    result = await _chat(
        db_session, org_a, user_a,
        "declare a variable called ticket_id as string in Lead intake",
    )

    # Typed variable edits persist through the same validated edit path.
    assert result.actions[0].type == "edit"
    assert result.actions[0].status == "ok"
    latest = (
        await db_session.execute(
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == "v2",
            )
        )
    ).scalar_one()
    assert latest.definition["variables"][0]["name"] == "ticket_id"


async def test_chat_deletes_a_workflow(db_session, org_a, user_a, allow_permissions):
    workflow = await _workflow(db_session, org_a)

    asked = await _chat(db_session, org_a, user_a, "delete the Lead intake workflow")
    assert asked.actions[0].type == "delete"
    assert asked.actions[0].status == "confirmation_required"
    await db_session.refresh(workflow)
    assert workflow.deleted_at is None

    result = await _chat(
        db_session, org_a, user_a,
        f"confirm {asked.actions[0].data['confirmation_token']}",
    )
    assert result.actions[0].type == "delete"
    assert result.actions[0].status == "ok"
    await db_session.refresh(workflow)
    assert workflow.deleted_at is not None


async def test_chat_reports_missing_delete_permission(
    db_session, org_a, user_a, deny_delete,
):
    workflow = await _workflow(db_session, org_a)

    result = await _chat(db_session, org_a, user_a, "delete the Lead intake workflow")

    assert result.actions[0].type == "delete"
    assert result.actions[0].status == "failed"
    assert "permission" in result.actions[0].detail.lower()
    await db_session.refresh(workflow)
    assert workflow.deleted_at is None


async def test_chat_lists_credentials_without_secrets(
    db_session, org_a, user_a, allow_permissions,
):
    db_session.add(
        Credential(
            organization_id=org_a.id,
            name="Slack bot",
            provider="slack",
            credential_type=CredentialType.API_KEY,
            encrypted_data="ciphertext",
        )
    )
    await db_session.commit()

    result = await _chat(db_session, org_a, user_a, "show credentials")

    assert result.actions[0].type == "list_credentials"
    assert result.actions[0].status == "ok"
    assert result.actions[0].data["credentials"][0]["name"] == "Slack bot"
    assert "ciphertext" not in str(result.actions[0].data)


async def test_chat_lists_agents(db_session, org_a, user_a, allow_permissions):
    db_session.add(
        Agent(
            organization_id=org_a.id,
            name="Invoice summarizer",
            slug="invoice-summarizer",
            agent_type=AgentType.CHAT,
        )
    )
    await db_session.commit()

    result = await _chat(db_session, org_a, user_a, "list agents")

    assert result.actions[0].type == "list_agents"
    assert result.actions[0].data["agents"][0]["name"] == "Invoice summarizer"


async def test_chat_tests_one_node(db_session, org_a, user_a, allow_permissions):
    workflow = await _workflow(db_session, org_a)

    result = await _chat(
        db_session, org_a, user_a, 'test the "Set values" node in Lead intake',
    )

    action = result.actions[0]
    assert action.type == "test_node"
    assert action.status == "ok"
    assert action.workflow_id == str(workflow.id)
    assert action.data["node_name"] == "Set values"
    assert action.data["outputs"] == {"ok": True}
    assert "succeeded" in action.detail


async def test_chat_renames_a_workflow(db_session, org_a, user_a, allow_permissions):
    workflow = await _workflow(db_session, org_a)

    result = await _chat(
        db_session, org_a, user_a, 'rename the workflow to "Intake v2"',
    )

    action = result.actions[0]
    assert action.type == "update_workflow"
    assert action.status == "ok"
    await db_session.refresh(workflow)
    assert workflow.name == "Intake v2"
    assert workflow.slug == "intake-v2"


async def test_chat_links_subworkflow_and_rejects_cycles(
    db_session, org_a, user_a, allow_permissions
):
    child = await _workflow(db_session, org_a, "Child flow")
    parent = await _workflow(
        db_session,
        org_a,
        "Parent flow",
        definition=_definition(
            nodes=[
                {
                    "id": "n1",
                    "type": "subworkflow",
                    "name": "Run child",
                    "config": {"workflow_id": str(uuid4())},
                }
            ]
        ),
    )

    response = await _chat(
        db_session, org_a, user_a, "link Parent flow to Child flow sub-workflow"
    )
    assert response.actions and response.actions[0].status == "ok", response
    latest = (
        await db_session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == parent.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert latest.definition["nodes"][0]["config"]["workflow_id"] == str(child.id)

    # A child that calls its parent makes the reverse link a cycle.
    db_session.add(
        WorkflowVersion(
            workflow_id=child.id,
            version="v2",
            schema_version="1.1",
            definition=_definition(
                nodes=[
                    {
                        "id": "n1",
                        "type": "subworkflow",
                        "name": "Back",
                        "config": {"workflow_id": str(parent.id)},
                    }
                ]
            ),
        )
    )
    await db_session.commit()

    blocked = await _chat(
        db_session, org_a, user_a, "link Parent flow to Child flow sub-workflow"
    )
    assert blocked.actions[0].status == "failed"
    assert "cycle" in blocked.actions[0].detail.lower()


async def test_chat_sets_workflow_static_data(
    db_session, org_a, user_a, allow_permissions
):
    workflow = await _workflow(db_session, org_a, "Counter flow")
    request = Request(
        {"type": "http", "method": "POST", "path": "/copilot/chat", "headers": []}
    )

    response = await koro_module._apply_action(
        db=db_session,
        request=request,
        organization_id=org_a.id,
        user_id=user_a.id,
        action={
            "type": "set_static_data",
            "workflow": "Counter flow",
            "scope": "global",
            "values": {"last_id": 42},
        },
        workflows=[workflow],
        current_id=None,
        trigger_input=None,
    )

    assert response.status == "ok", response
    await db_session.refresh(workflow)
    assert workflow.static_data["global"]["last_id"] == 42


async def test_chat_sets_operations_settings(db_session, org_a, user_a, allow_permissions):
    workflow = await _workflow(db_session, org_a)
    handler = await _workflow(db_session, org_a, name="Failure handler")

    concurrency = await _chat(
        db_session, org_a, user_a, "set concurrency to 2 for Lead intake",
    )
    assert concurrency.actions[0].status == "ok"

    failure = await _chat(
        db_session, org_a, user_a,
        "when Lead intake fails run Failure handler",
    )
    assert failure.actions[0].status == "ok"

    latest = (
        await db_session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert latest.definition["settings"]["max_concurrency"] == 2
    assert latest.definition["settings"]["error_workflow_id"] == str(handler.id)


async def test_chat_transcript_keeps_run_links_but_not_definitions(
    db_session, org_a, user_a, allow_permissions,
):
    await _workflow(db_session, org_a)

    result = await _chat(db_session, org_a, user_a, "run Lead intake", persist=True)
    assert result.conversation_id
    execution_id = result.actions[0].data["execution_id"]

    detail = await koro_module.koro_conversation(
        None,
        org_a.id,
        UUID(result.conversation_id),
        auth_context=types.SimpleNamespace(user_id=user_a.id),
        db=db_session,
    )
    stored = detail.messages[1].actions[0]
    assert stored["data"]["execution_id"] == execution_id
    # Generated definitions stay out of the transcript.
    assert "definition" not in stored


async def test_chat_batch_publishes_then_runs(
    db_session, org_a, user_a, allow_permissions,
):
    workflow = await _workflow(db_session, org_a)

    result = await _chat(db_session, org_a, user_a, "publish and run Lead intake")

    assert [action.type for action in result.actions] == ["publish", "run"]
    assert all(action.status == "ok" for action in result.actions)
    await db_session.refresh(workflow)
    assert workflow.status == WorkflowStatus.ACTIVE
    executions = (
        await db_session.execute(
            select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow.id)
        )
    ).scalars().all()
    assert len(executions) == 1
    assert executions[0].status == WorkflowExecutionStatus.COMPLETED


def test_plan_routes_coverage_questions():
    workflow = types.SimpleNamespace(
        id=uuid4(), name="Lead intake", slug="lead-intake", status=WorkflowStatus.ACTIVE,
    )

    plan = _command_plan("can you do Slack in Lead intake?", [workflow], None)

    assert plan["action"]["type"] == "node_coverage"
    assert "Slack" in plan["action"]["query"]


async def test_chat_answers_capability_questions_honestly(
    db_session, org_a, user_a, allow_permissions,
):
    built_in = await _chat(db_session, org_a, user_a, "can you hash a value?")
    assert built_in.actions[0].type == "node_coverage"
    assert built_in.actions[0].status == "ok"
    assert built_in.actions[0].data["matches"]

    airtable = await _chat(db_session, org_a, user_a, "do you support Airtable?")
    assert airtable.actions[0].type == "node_coverage"
    assert "not built in" in airtable.actions[0].detail
    assert "HTTP Request" in airtable.actions[0].detail
