"""Assistant transcripts: chat-history helpers and owner scoping."""

import types
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from openagent.api.v1 import koro as koro_module
from openagent.api.v1.koro import (
    KoroActionResponse,
    KoroChatRequest,
    KoroChatResponse,
    _conversation_title,
    _load_conversation,
    _persist_turn,
)
from openagent.api.v1.koro import router as koro_router
from openagent.db.models import Conversation, Message


def test_conversation_title_is_one_short_line():
    assert (
        _conversation_title("  Build   a workflow\nthat triages emails  ")
        == "Build a workflow that triages emails"
    )
    assert _conversation_title("") == "New chat"

    title = _conversation_title("x" * 120)
    assert len(title) <= 80
    assert title.endswith("…")


def test_history_routes_are_registered():
    paths = {route.path for route in koro_router.routes}
    base = "/organizations/{organization_id}/copilot/conversations"
    assert base in paths
    assert f"{base}/{{conversation_id}}" in paths


def test_chat_request_accepts_optional_context():
    assert KoroChatRequest(message="hi").context is None
    request = KoroChatRequest(
        message="hi",
        context={"route": "/workflows/w1/edit", "workflow": {"id": "w1"}},
    )
    assert request.context == {"route": "/workflows/w1/edit", "workflow": {"id": "w1"}}


def test_context_block_is_capped_and_descriptive():
    assert koro_module._context_block(None) == ""
    assert koro_module._context_block({}) == ""

    block = koro_module._context_block(
        {"route": "/workflows/w1/edit", "workflow": {"id": "w1", "nodes": 2}}
    )
    assert "/workflows/w1/edit" in block
    assert '"nodes": 2' in block
    assert "read-only" in block

    huge = koro_module._context_block(
        {"blob": "x" * (koro_module.MAX_CONTEXT_CHARS * 2)}
    )
    assert huge.endswith("…")
    assert len(huge) < koro_module.MAX_CONTEXT_CHARS + 250


async def _conversation(db_session, org, user, title=None):
    conversation = Conversation(
        organization_id=org.id, user_id=user.id, title=title, metadata={},
    )
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)
    return conversation


async def test_persist_turn_appends_the_exchange(db_session, org_a, user_a):
    conversation = await _conversation(db_session, org_a, user_a)
    workflow_id = str(uuid4())
    response = KoroChatResponse(
        reply="Built it with 3 nodes.",
        source="commands",
        actions=[
            KoroActionResponse(
                type="build",
                status="ok",
                detail="Built “Lead intake” with 3 nodes.",
                workflow_id=workflow_id,
            ),
        ],
    )

    await _persist_turn(db_session, conversation, "build an automation for leads", response)

    await db_session.refresh(conversation)
    assert conversation.title == "build an automation for leads"

    result = await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    )
    messages = list(result.scalars().all())
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert messages[0].content == "build an automation for leads"
    assert messages[1].metadata["source"] == "commands"
    assert messages[1].metadata["actions"][0]["workflow_id"] == workflow_id


async def test_persist_turn_keeps_an_existing_title(db_session, org_a, user_a):
    conversation = await _conversation(db_session, org_a, user_a, title="Lead intake")

    await _persist_turn(
        db_session,
        conversation,
        "add a Slack notification",
        KoroChatResponse(reply="Added it.", source="model"),
    )

    await db_session.refresh(conversation)
    assert conversation.title == "Lead intake"


async def test_conversation_is_scoped_to_its_owner(db_session, org_a, user_a, user_b):
    conversation = await _conversation(db_session, org_a, user_a)

    with pytest.raises(HTTPException) as denied:
        await _load_conversation(db_session, org_a.id, user_b.id, conversation.id)
    assert denied.value.status_code == 404

    loaded = await _load_conversation(db_session, org_a.id, user_a.id, conversation.id)
    assert loaded.id == conversation.id


@pytest.fixture
async def allow_permissions(monkeypatch):
    """Route bodies re-check permissions against the request session; bypass that."""

    async def allow(*args, **kwargs):
        return None

    monkeypatch.setattr(koro_module, "require_permission", lambda _permission: allow)


async def test_history_routes_list_and_replay_a_transcript(
    db_session, org_a, user_a, allow_permissions,
):
    conversation = await _conversation(db_session, org_a, user_a, title="Lead intake")
    workflow_id = str(uuid4())
    await _persist_turn(
        db_session,
        conversation,
        "add a Slack notification",
        KoroChatResponse(
            reply="Updated the workflow.",
            source="model",
            actions=[KoroActionResponse(
                type="edit", status="ok", detail="Updated “Lead intake”.",
                workflow_id=workflow_id,
            )],
        ),
    )
    context = types.SimpleNamespace(user_id=user_a.id)

    rows = await koro_module.koro_conversations(
        None, org_a.id, auth_context=context, db=db_session,
    )

    assert [row.id for row in rows] == [str(conversation.id)]
    assert rows[0].title == "Lead intake"
    assert rows[0].message_count == 2

    detail = await koro_module.koro_conversation(
        None, org_a.id, conversation.id, auth_context=context, db=db_session,
    )

    assert [message.role for message in detail.messages] == ["user", "assistant"]
    assert detail.messages[1].source == "model"
    assert detail.messages[1].actions[0]["workflow_id"] == workflow_id


async def test_chat_persists_and_continues_a_transcript(
    db_session, org_a, user_a, allow_permissions,
):
    context = types.SimpleNamespace(user_id=user_a.id)
    request = Request({"type": "http", "method": "POST", "path": "/copilot/chat",
                       "headers": []})

    first = await koro_module.koro_chat(
        request,
        org_a.id,
        KoroChatRequest(message="list my workflows", persist=True),
        auth_context=context,
        db=db_session,
    )
    assert first.conversation_id

    second = await koro_module.koro_chat(
        request,
        org_a.id,
        KoroChatRequest(
            message="list my workflows",
            persist=True,
            conversation_id=UUID(first.conversation_id),
        ),
        auth_context=context,
        db=db_session,
    )
    assert second.conversation_id == first.conversation_id

    detail = await koro_module.koro_conversation(
        None, org_a.id, UUID(first.conversation_id), auth_context=context, db=db_session,
    )
    assert detail.message_count == 4
    assert detail.messages[0].role == "user"
    assert detail.messages[0].content == "list my workflows"
    assert detail.messages[1].actions[0]["type"] == "list"


async def test_history_routes_rename_and_delete(
    db_session, org_a, user_a, allow_permissions,
):
    conversation = await _conversation(db_session, org_a, user_a, title="Old name")
    context = types.SimpleNamespace(user_id=user_a.id)

    renamed = await koro_module.koro_rename_conversation(
        None,
        org_a.id,
        conversation.id,
        koro_module.ConversationRename(title="  Support   triage "),
        auth_context=context,
        db=db_session,
    )
    assert renamed.title == "Support triage"

    await koro_module.koro_delete_conversation(
        None, org_a.id, conversation.id, auth_context=context, db=db_session,
    )

    rows = await koro_module.koro_conversations(
        None, org_a.id, auth_context=context, db=db_session,
    )
    assert rows == []
    with pytest.raises(HTTPException):
        await koro_module.koro_conversation(
            None, org_a.id, conversation.id, auth_context=context, db=db_session,
        )
