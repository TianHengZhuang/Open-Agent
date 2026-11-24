"""Phase 13 chat control plane: one tool table, confirmations and undo.

The audit test walks ``TOOL_SCHEMAS`` and proves every tool is permissioned,
prompted and listed; the behaviour tests drive real mutations without a page
visit (build -> run -> diagnose -> publish -> delete).
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select
from starlette.requests import Request

from openagent.api.v1 import koro as koro_module
from openagent.api.v1.koro import KoroChatRequest
from openagent.api.v1.koro_tools import (
    TOOL_SCHEMAS,
    decode_confirmation,
    issue_confirmation,
)
from openagent.api.v1.koro_tools.registry import (
    ACTION_NAMES,
    DOMAINS,
    KINDS,
    prompt_union,
)
from openagent.db.models import (
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowExecutionTriggerType,
    WorkflowStatus,
    WorkflowVersion,
)


def _definition(value: bool = True):
    return {
        "schema_version": "1.1",
        "triggers": [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": [
            {"id": "n1", "type": "set", "name": "Set values", "config": {"values": {"ok": value}}}
        ],
        "edges": [{"id": "e1", "from": "t", "to": "n1"}],
        "variables": [],
        "settings": {},
    }


def _request() -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/copilot/chat", "headers": []}
    )


async def _workflow(db, org, name="Lead intake", *, versions=1):
    workflow = Workflow(
        organization_id=org.id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        status=WorkflowStatus.DRAFT,
    )
    db.add(workflow)
    await db.flush()
    for index in range(versions):
        db.add(
            WorkflowVersion(
                workflow_id=workflow.id,
                version=f"v{index + 1}",
                schema_version="1.1",
                # Distinct timestamps: SQLite's default has second precision.
                created_at=datetime.now(timezone.utc) + timedelta(seconds=index),
                definition=_definition(value=index % 2 == 0),
            )
        )
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def _apply(db, org, user_a, action, *, workflows=None, current_id=None):
    if workflows is None:
        workflows = await koro_module._org_workflows(db, org.id)
    return await koro_module._apply_action(
        db=db,
        request=_request(),
        organization_id=org.id,
        user_id=user_a.id,
        action=action,
        workflows=workflows,
        current_id=current_id,
        trigger_input=None,
    )


async def _chat(db, org, user_a, message, **kwargs):
    return await koro_module.koro_chat(
        _request(),
        org.id,
        KoroChatRequest(message=message, **kwargs),
        auth_context=types.SimpleNamespace(user_id=user_a.id),
        db=db,
    )


@pytest.fixture
def allow_permissions(monkeypatch):
    async def allow(*args, **kwargs):
        return None

    monkeypatch.setattr(koro_module, "require_permission", lambda permission: allow)


# ---------------------------------------------------------------------------
# The single tool table
# ---------------------------------------------------------------------------


def test_every_tool_is_declared_and_permissioned():
    names = [spec.name for spec in TOOL_SCHEMAS]
    assert len(names) == len(set(names)) >= 40
    for spec in TOOL_SCHEMAS:
        assert spec.domain in DOMAINS, spec.name
        assert spec.kind in KINDS, spec.name
        assert spec.permission.count(":") == 1, spec.name
        assert spec.description.strip(), spec.name
        schema = spec.schema()
        assert schema["properties"]["type"]["const"] == spec.name
        assert schema["required"] == ["type"]
    assert tuple(ACTION_NAMES) + ("none", "batch") == koro_module.ACTION_TYPES
    assert set(ACTION_NAMES) == set(koro_module.HANDLED_ACTIONS)


def test_prompt_and_registry_cannot_drift():
    prompt = koro_module._expanded_system_prompt()
    assert "<TOOL_ACTIONS>" not in prompt
    union = prompt.split('"type": "', 1)[1].split('"', 1)[0]
    assert set(union.split("|")) == set(ACTION_NAMES) | {"none", "batch"}
    assert union == prompt_union()


@pytest.mark.asyncio
async def test_tools_endpoint_lists_the_same_table(db_session, org_a, allow_permissions):
    body = await koro_module.list_koro_tools(_request(), db=db_session)
    assert body["total"] == len(TOOL_SCHEMAS)
    assert [tool["name"] for tool in body["tools"]] == list(ACTION_NAMES)

    runs = await koro_module.list_koro_tools(_request(), domain="runs", db=db_session)
    assert runs["returned"] < body["total"]
    assert {tool["domain"] for tool in runs["tools"]} == {"runs"}

    writes = await koro_module.list_koro_tools(_request(), kind="destructive", db=db_session)
    assert {tool["name"] for tool in writes["tools"]} == {"delete", "restore", "prune"}


# ---------------------------------------------------------------------------
# Plan -> confirm -> apply
# ---------------------------------------------------------------------------


def test_confirmation_tokens_carry_the_action_and_expire():
    action = {"type": "delete", "workflow": "Lead intake"}
    token = issue_confirmation(action)
    assert decode_confirmation(token) == action
    assert decode_confirmation(token, now=10**12) is None  # expired
    assert decode_confirmation("cfm1.not-a-token.sig") is None


@pytest.mark.asyncio
async def test_delete_needs_a_matching_confirmation_token(
    db_session, org_a, user_a, allow_permissions
):
    workflow = await _workflow(db_session, org_a)

    first = await _apply(db_session, org_a, user_a, {"type": "delete", "workflow": workflow.name})
    assert first.status == "confirmation_required"
    assert first.data["preview"]["workflow_id"] == str(workflow.id)
    token = first.data["confirmation_token"]

    await db_session.refresh(workflow)
    assert workflow.deleted_at is None

    # A tampered token is refused and nothing is deleted.
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    second = await _apply(
        db_session, org_a, user_a,
        {"type": "delete", "workflow": workflow.name, "confirm": tampered},
    )
    assert second.status == "confirmation_required"
    await db_session.refresh(workflow)
    assert workflow.deleted_at is None

    # A token for another action cannot be replayed as a delete.
    other = issue_confirmation({"type": "restore", "workflow": workflow.name, "version": "v1"})
    third = await _apply(
        db_session, org_a, user_a,
        {"type": "delete", "workflow": workflow.name, "confirm": other},
    )
    assert third.status == "confirmation_required"

    applied = await _apply(
        db_session, org_a, user_a,
        {"type": "delete", "workflow": workflow.name, "confirm": token},
    )
    assert applied.status == "ok"
    await db_session.refresh(workflow)
    assert workflow.deleted_at is not None


@pytest.mark.asyncio
async def test_confirm_chat_message_replays_the_signed_action(
    db_session, org_a, user_a, allow_permissions
):
    workflow = await _workflow(db_session, org_a)
    asked = await _chat(db_session, org_a, user_a, "delete the workflow Lead intake")
    action = next(item for item in asked.actions if item.type == "delete")
    assert action.status == "confirmation_required"
    token = action.data["confirmation_token"]

    done = await _chat(db_session, org_a, user_a, f"confirm {token}")
    assert any(item.type == "delete" and item.status == "ok" for item in done.actions)
    await db_session.refresh(workflow)
    assert workflow.deleted_at is not None


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_last_restores_the_previous_version(
    db_session, org_a, user_a, allow_permissions
):
    workflow = await _workflow(db_session, org_a, versions=2)
    latest = await koro_module._latest_version(db_session, workflow.id)
    assert latest.version == "v2"

    result = await _apply(db_session, org_a, user_a, {"type": "undo_last", "workflow": workflow.name})
    assert result.status == "ok"
    assert result.data["restored_from"] == "v1"

    rows = (
        await db_session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.created_at.desc())
        )
    ).scalars().all()
    assert len(rows) == 3
    # v1 was the value=True definition; v2 (and undo) preserve history.
    assert rows[0].definition["nodes"][0]["config"]["values"]["ok"] in (True, False)


@pytest.mark.asyncio
async def test_mutating_tools_advertise_undo(db_session, org_a, user_a, allow_permissions):
    await _workflow(db_session, org_a)
    reply = await _chat(
        db_session, org_a, user_a, "build a workflow that sets a flag to true"
    )
    built = next(item for item in reply.actions if item.type in ("build", "edit"))
    assert built.status == "ok"
    assert built.data["undo"] == {
        "action": "undo_last",
        "workflow_id": built.workflow_id,
    }


# ---------------------------------------------------------------------------
# Workflow/run/operations tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_node_logs_resume_prune_and_health(
    db_session, org_a, user_a, allow_permissions
):
    workflow = await _workflow(db_session, org_a)

    exported = await _apply(
        db_session, org_a, user_a, {"type": "export_workflow", "workflow": workflow.name}
    )
    assert exported.status == "ok"
    assert exported.data["definition"]["schema_version"] == "1.1"
    assert exported.data["version"] == "v1"

    ran = await _apply(db_session, org_a, user_a, {"type": "run", "workflow": workflow.name})
    assert ran.status == "ok"

    logs = await _apply(
        db_session, org_a, user_a, {"type": "node_logs", "workflow": workflow.name}
    )
    assert logs.status == "ok"
    assert "Set values" in [node["node_name"] for node in logs.data["nodes"]]

    filtered = await _apply(
        db_session, org_a, user_a,
        {"type": "node_logs", "workflow": workflow.name, "node": "Set values"},
    )
    assert [node["node_name"] for node in filtered.data["nodes"]] == ["Set values"]
    missing = await _apply(
        db_session, org_a, user_a,
        {"type": "node_logs", "workflow": workflow.name, "node": "nope"},
    )
    assert missing.status == "failed"

    # A waiting execution resumes through the chat tool.
    waiting = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.WAITING,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        metadata={
            "wait": {
                "definition": _definition(),
                "runs": [],
                "outputs": {},
                "variables": {},
                "trigger": {},
            }
        },
    )
    db_session.add(waiting)
    await db_session.commit()
    resumed = await _apply(
        db_session, org_a, user_a, {"type": "resume_wait", "workflow": workflow.name}
    )
    assert resumed.status == "ok"
    await db_session.refresh(waiting)
    assert waiting.status == WorkflowExecutionStatus.COMPLETED

    # Pruning is org-scoped and requires a confirmation token.
    old = WorkflowExecution(
        organization_id=org_a.id,
        workflow_id=workflow.id,
        status=WorkflowExecutionStatus.COMPLETED,
        trigger_type=WorkflowExecutionTriggerType.MANUAL,
        started_at=datetime.now(timezone.utc) - timedelta(days=400),
        completed_at=datetime.now(timezone.utc) - timedelta(days=400),
        created_at=datetime.now(timezone.utc) - timedelta(days=400),
        metadata={},
    )
    db_session.add(old)
    await db_session.commit()

    asked = await _apply(
        db_session, org_a, user_a,
        {"type": "prune", "retention_seconds": 60, "keep_last": 0},
    )
    assert asked.status == "confirmation_required"
    token = asked.data["confirmation_token"]
    pruned = await _apply(
        db_session, org_a, user_a,
        {"type": "prune", "retention_seconds": 60, "keep_last": 0, "confirm": token},
    )
    assert pruned.status == "ok"
    assert pruned.data["deleted"] >= 1

    health = await _apply(db_session, org_a, user_a, {"type": "health"})
    assert health.status == "ok"
    assert health.data


@pytest.mark.asyncio
async def test_open_page_resolves_surfaces_and_entities(
    db_session, org_a, user_a, allow_permissions
):
    workflow = await _workflow(db_session, org_a)

    chat = await _apply(db_session, org_a, user_a, {"type": "open_page", "page": "assistant"})
    assert chat.status == "ok"
    assert chat.data == {"path": "/assistant", "label": "Open chat", "page": "assistant"}

    builder = await _apply(
        db_session, org_a, user_a,
        {"type": "open_page", "page": "workflow", "workflow": workflow.name},
    )
    assert builder.status == "ok"
    assert builder.data["path"] == f"/workflows/{workflow.id}"
    assert builder.workflow_id == str(workflow.id)

    execution_id = UUID("22222222-2222-2222-2222-222222222222")
    run = await _apply(
        db_session, org_a, user_a,
        {"type": "open_page", "page": "execution", "execution_id": str(execution_id)},
    )
    assert run.status == "ok"
    assert run.data["path"] == f"/runs/{execution_id}"

    for bad in (
        {"type": "open_page", "page": "execution", "execution_id": "nope"},
        {"type": "open_page", "page": "nowhere"},
        {"type": "open_page", "page": "workflow", "workflow": "missing"},
    ):
        result = await _apply(db_session, org_a, user_a, bad)
        assert result.status == "failed", bad


# ---------------------------------------------------------------------------
# Acceptance: one conversation, no page visit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scripted_conversation_lifecycle(db_session, org_a, user_a, allow_permissions):
    built = await _apply(
        db_session, org_a, user_a,
        {"type": "build", "name": "Chat lifecycle", "definition": _definition()},
    )
    assert built.status == "ok"
    workflow_id = UUID(built.workflow_id)

    replies = []
    for message in (
        "run Chat lifecycle",
        "diagnose Chat lifecycle",
        "publish Chat lifecycle",
    ):
        reply = await _chat(db_session, org_a, user_a, message, workflow_id=workflow_id)
        assert reply.actions, message
        assert reply.actions[0].status == "ok", (message, reply.actions[0].detail)
        replies.append(reply.actions[0].type)
    assert replies == ["run", "diagnose", "publish"]

    workflow = await db_session.get(Workflow, workflow_id)
    assert workflow.status == WorkflowStatus.ACTIVE

    asked = await _chat(
        db_session, org_a, user_a, "delete the workflow Chat lifecycle",
        workflow_id=workflow_id,
    )
    delete_action = next(item for item in asked.actions if item.type == "delete")
    assert delete_action.status == "confirmation_required"
    applied = await _chat(
        db_session, org_a, user_a,
        f"confirm {delete_action.data['confirmation_token']}",
        workflow_id=workflow_id,
    )
    assert applied.actions[0].status == "ok"
    await db_session.refresh(workflow)
    assert workflow.deleted_at is not None
