"""Chat trigger payloads, prompt-node execution and SSE chat plumbing."""

import json

import httpx
import pytest

from openagent.runtime import local_engine
from openagent.services import model_runtime, workflow_chat
from openagent.services.model_runtime import ModelTarget, ModelRuntimeError
from openagent.services.workflow_definition import validate_definition


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def test_chat_item_matches_n8n_shape():
    item = workflow_chat.build_chat_item("Hello", "session-1")
    assert item == {
        "json": {
            "chatInput": "Hello",
            "sessionId": "session-1",
            "action": "sendMessage",
        }
    }


def test_chat_item_keeps_valid_files_and_drops_the_rest():
    item = workflow_chat.build_chat_item(
        "Look",
        "s1",
        files=[
            {"name": "notes.txt", "mime_type": "text/plain", "data_url": "data:text/plain;base64,aGk="},
            {"name": "evil.txt", "mime_type": "text/plain", "data_url": "https://example.com/x"},
            {"name": "", "mime_type": "text/plain", "data_url": "data:text/plain;base64,aGk="},
            {"name": "huge.bin", "mime_type": "application/octet-stream", "data_url": "data:x;base64," + "A" * 4_000_001},
        ],
    )
    files = item["json"]["files"]
    assert len(files) == 1
    assert files[0]["name"] == "notes.txt"
    assert files[0]["mimeType"] == "text/plain"


def test_chat_item_carries_metadata_only_when_given():
    assert "metadata" not in workflow_chat.build_chat_item("hi", "s")["json"]
    assert workflow_chat.build_chat_item("hi", "s", metadata={"a": 1})["json"]["metadata"] == {"a": 1}


def test_history_is_trimmed_and_validated():
    history = [
        {"role": "user", "content": "one"},
        {"role": "system", "content": "not from a transcript"},
        {"role": "assistant", "content": "   "},
        {"role": "assistant", "content": "two"},
        {"role": "tool", "content": "three"},
        {"role": "user"},
    ]
    assert workflow_chat.clean_history(history) == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]


def test_chat_trigger_input_wraps_item_and_history():
    payload = workflow_chat.chat_trigger_input(
        "Hi", "s1", history=[{"role": "user", "content": "earlier"}]
    )
    assert payload["json"]["chatInput"] == "Hi"
    assert payload["json"]["sessionId"] == "s1"
    assert payload["history"] == [{"role": "user", "content": "earlier"}]
    assert payload["message"] == "Hi"


def test_chat_trigger_is_a_valid_trigger_type():
    definition = {
        "schema_version": "1.0",
        "triggers": [{"id": "chat_in", "type": "chat", "name": "Chat", "config": {}}],
        "nodes": [],
        "edges": [],
    }
    result = validate_definition(definition)
    assert result.valid, [issue.code for issue in result.errors]


# ---------------------------------------------------------------------------
# Reply extraction / SSE framing
# ---------------------------------------------------------------------------


def test_extract_reply_prefers_the_model_node():
    runs = [
        {"node_id": "chat_in", "node_type": "chat", "status": "succeeded", "outputs": {"json": {}}},
        {"node_id": "shape", "node_type": "set", "status": "succeeded", "outputs": {"text": "set text"}},
        {"node_id": "reply", "node_type": "prompt", "status": "succeeded", "outputs": {"text": "model text"}},
    ]
    assert workflow_chat.extract_reply(runs) == ("model text", "reply")


def test_extract_reply_falls_back_to_any_text_output():
    runs = [
        {"node_id": "chat_in", "node_type": "chat", "status": "succeeded", "outputs": {}},
        {"node_id": "shape", "node_type": "set", "status": "succeeded", "outputs": {"reply": "from set"}},
        {"node_id": "failed", "node_type": "prompt", "status": "failed", "outputs": {"text": "nope"}},
    ]
    assert workflow_chat.extract_reply(runs) == ("from set", "shape")


def test_extract_reply_is_empty_when_nothing_replied():
    assert workflow_chat.extract_reply([{"node_id": "a", "status": "skipped", "outputs": {}}]) == ("", None)


def test_sse_frames_are_single_line_json():
    frame = workflow_chat.sse("token", {"text": "line\nbreak"})
    assert frame.startswith("event: token\ndata: ")
    assert frame.endswith("\n\n")
    body = json.loads(frame.split("data: ", 1)[1].strip())
    assert body == {"text": "line\nbreak"}


def test_summarize_runs_keeps_the_trace_fields():
    steps = workflow_chat.summarize_runs(
        [
            {
                "node_id": "n1",
                "node_type": "prompt",
                "node_name": "Reply",
                "status": "succeeded",
                "duration_ms": 12,
                "outputs": {"text": "secret"},
                "error": None,
                "reason": None,
                "inputs": {"input": {}},
            }
        ]
    )
    assert steps == [
        {
            "node_id": "n1",
            "node_type": "prompt",
            "node_name": "Reply",
            "status": "succeeded",
            "duration_ms": 12,
            "error": None,
            "reason": None,
        }
    ]


# ---------------------------------------------------------------------------
# Engine: chat trigger + prompt node
# ---------------------------------------------------------------------------


def chat_definition(prompt: str = "{{json.chatInput}}") -> dict:
    return {
        "schema_version": "1.0",
        "triggers": [{"id": "chat_in", "type": "chat", "name": "Chat Trigger", "config": {}}],
        "nodes": [
            {
                "id": "reply",
                "type": "prompt",
                "name": "Reply",
                "config": {"system": "You are terse.", "prompt": prompt},
                "position": {"x": 300, "y": 80},
            }
        ],
        "edges": [
            {"id": "e1", "from": "chat_in", "to": "reply", "condition": {"when": "always"}}
        ],
    }


async def test_chat_trigger_item_reaches_the_prompt_node(monkeypatch):
    seen = {}

    async def fake_stream(target, messages, *, on_delta=None, **kwargs):
        seen["messages"] = messages
        seen["model"] = target.model
        if on_delta is not None:
            await on_delta("Hel")
            await on_delta("lo")
        return "Hello"

    monkeypatch.setattr(local_engine, "chat_stream", fake_stream)

    target = ModelTarget(base_url="http://127.0.0.1:11434/v1", model="qwen2.5:0.5b-instruct")
    deltas = []

    async def sink(text: str) -> None:
        deltas.append(text)

    payload = workflow_chat.chat_trigger_input(
        "What is 2+2?",
        "s1",
        history=[
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ],
    )

    runs, output, status = await local_engine.execute_definition(
        chat_definition(), payload, model=target, token_sink=sink
    )

    assert status == "completed"
    by_id = {run["node_id"]: run for run in runs}
    assert by_id["chat_in"]["outputs"]["json"]["chatInput"] == "What is 2+2?"
    assert by_id["chat_in"]["outputs"]["json"]["sessionId"] == "s1"
    assert by_id["reply"]["status"] == "succeeded"
    assert by_id["reply"]["outputs"]["text"] == "Hello"
    assert deltas == ["Hel", "lo"]
    assert seen["model"] == "qwen2.5:0.5b-instruct"
    assert seen["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "What is 2+2?"},
    ]
    assert workflow_chat.extract_reply(runs) == ("Hello", "reply")
    assert output["terminal"]["reply"]["text"] == "Hello"


async def test_prompt_node_without_a_model_is_skipped_not_faked():
    runs, _output, _status = await local_engine.execute_definition(
        chat_definition(), workflow_chat.chat_trigger_input("Hi", "s1")
    )
    reply = next(run for run in runs if run["node_id"] == "reply")
    assert reply["status"] == "skipped"
    assert reply["reason"] == "SKIPPED_REQUIRES_PROMPT"


async def test_prompt_node_reports_model_failures(monkeypatch):
    async def failing(target, messages, **kwargs):
        raise ModelRuntimeError("connection refused")

    monkeypatch.setattr(local_engine, "chat_stream", failing)
    target = ModelTarget(base_url="http://127.0.0.1:11434/v1", model="m")

    runs, _output, status = await local_engine.execute_definition(
        chat_definition(), workflow_chat.chat_trigger_input("Hi", "s1"), model=target
    )
    reply = next(run for run in runs if run["node_id"] == "reply")
    assert status == "failed"
    assert reply["status"] == "failed"
    assert reply["error_code"] == "MODEL_ERROR"
    assert "connection refused" in reply["error"]


async def test_prompt_falls_back_to_chat_input_when_prompt_is_empty(monkeypatch):
    captured = {}

    async def fake_stream(target, messages, **kwargs):
        captured["messages"] = messages
        return "ok"

    monkeypatch.setattr(local_engine, "chat_stream", fake_stream)
    target = ModelTarget(base_url="http://127.0.0.1:11434/v1", model="m")
    await local_engine.execute_definition(
        chat_definition(prompt=""), workflow_chat.chat_trigger_input("plain input", "s1"), model=target
    )
    assert captured["messages"][-1] == {"role": "user", "content": "plain input"}


# ---------------------------------------------------------------------------
# Model runtime client
# ---------------------------------------------------------------------------


def test_normalize_chat_base_forms():
    assert model_runtime.normalize_chat_base("http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1"
    assert model_runtime.normalize_chat_base("http://127.0.0.1:11434/") == "http://127.0.0.1:11434/v1"
    assert model_runtime.normalize_chat_base("http://localhost:1234/v1") == "http://localhost:1234/v1"
    assert (
        model_runtime.normalize_chat_base("https://api.openai.com/v1/chat/completions")
        == "https://api.openai.com/v1"
    )


def test_describe_endpoint_labels_local_runtimes():
    assert model_runtime.describe_endpoint("http://127.0.0.1:11434/v1") == ("Ollama", True)
    assert model_runtime.describe_endpoint("http://localhost:1234/v1") == ("LM Studio", True)
    provider, local = model_runtime.describe_endpoint("https://api.openai.com/v1")
    assert provider == "OpenAI" and local is False


async def test_chat_stream_collects_streaming_deltas():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["model"] == "local-model"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    target = ModelTarget(base_url="http://127.0.0.1:1234/v1", model="local-model")
    deltas = []

    async def sink(text: str) -> None:
        deltas.append(text)

    reply = await model_runtime.chat_stream(
        target,
        [{"role": "user", "content": "hi"}],
        on_delta=sink,
        transport=httpx.MockTransport(handler),
    )
    assert reply == "Hello"
    assert deltas == ["Hel", "lo"]


async def test_chat_stream_buffers_without_a_sink():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "buffered"}}]},
        )

    target = ModelTarget(base_url="http://127.0.0.1:1234/v1", model="local-model")
    reply = await model_runtime.chat_stream(
        target, [{"role": "user", "content": "hi"}], transport=httpx.MockTransport(handler)
    )
    assert reply == "buffered"


async def test_chat_stream_surfaces_provider_errors():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    async def sink(text: str) -> None:
        raise AssertionError("no deltas expected on an error response")

    target = ModelTarget(base_url="https://api.openai.com/v1", model="gpt-x", api_key="bad")
    with pytest.raises(ModelRuntimeError) as caught:
        await model_runtime.chat_stream(
            target,
            [{"role": "user", "content": "hi"}],
            on_delta=sink,
            transport=httpx.MockTransport(handler),
        )
    assert "invalid api key" in str(caught.value)
