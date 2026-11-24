"""Phase 7: node-level execution semantics.

Covers n8n's node settings: retry (retryOnFail/maxTries/waitBetweenTries),
onError continueErrorOutput routing, alwaysOutputData, executeOnce and the
matching validation rules.
"""

from __future__ import annotations

import asyncio

import pytest

from openagent.runtime import local_engine
from openagent.services.workflow_definition import validate_definition


def _definition(nodes, edges, *, triggers=None, settings=None):
    return {
        "schema_version": "1.1",
        "triggers": triggers
        or [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": edges,
        "variables": [],
        "settings": settings or {},
    }


def _run(definition, trigger_input=None):
    return asyncio.run(local_engine.execute_definition(definition, trigger_input or {}))


def _by_id(runs, node_id):
    return next(run for run in runs if run["node_id"] == node_id)


def _patch_failures(monkeypatch, fail_times):
    """Make the node with id ``flaky`` fail its first N executions."""
    calls = {"count": 0}
    original = local_engine._exec_node

    async def wrapped(node, inputs, ctx, items=None):
        if node.get("id") == "flaky":
            calls["count"] += 1
            if calls["count"] <= fail_times:
                return local_engine.NodeOutcome(
                    status="failed", error="temporarily unavailable", error_code="BOOM"
                )
        return await original(node, inputs, ctx, items=items)

    monkeypatch.setattr(local_engine, "_exec_node", wrapped)
    return calls


def _flaky_definition(**settings):
    node = {
        "id": "flaky",
        "type": "transform",
        "name": "Flaky",
        "config": {"mapping": {"ok": True}},
        **settings,
    }
    return _definition([node], [{"id": "e1", "from": "t", "to": "flaky"}])


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_retry_succeeds_after_transient_failures(monkeypatch):
    calls = _patch_failures(monkeypatch, 2)

    runs, _, status = _run(
        _flaky_definition(retry_on_fail=True, max_tries=3, wait_between_tries_ms=0)
    )

    node = _by_id(runs, "flaky")
    assert status == "completed"
    assert calls["count"] == 3
    assert node["attempt"] == 3
    assert node["status"] == "succeeded"


def test_retry_exhaustion_fails_with_attempt_count(monkeypatch):
    calls = _patch_failures(monkeypatch, 5)

    runs, _, status = _run(
        _flaky_definition(retry_on_fail=True, max_tries=3, wait_between_tries_ms=0)
    )

    node = _by_id(runs, "flaky")
    assert status == "failed"
    assert calls["count"] == 3
    assert node["attempt"] == 3
    assert node["error"] == "temporarily unavailable"


def test_retry_without_the_flag_does_not_retry(monkeypatch):
    calls = _patch_failures(monkeypatch, 5)

    runs, _, status = _run(_flaky_definition(max_tries=3))  # max_tries alone: 1 try

    node = _by_id(runs, "flaky")
    assert status == "failed"
    assert calls["count"] == 1
    assert node["attempt"] == 1


def test_legacy_retry_policy_still_works(monkeypatch):
    calls = _patch_failures(monkeypatch, 1)

    _, _, status = _run(_flaky_definition(retry_policy={"max_attempts": 2}))

    assert status == "completed"
    assert calls["count"] == 2


# ---------------------------------------------------------------------------
# on_error: continueErrorOutput
# ---------------------------------------------------------------------------


def _error_output_definition():
    return _definition(
        [
            {
                "id": "boom",
                "type": "transform",
                "name": "Boom",
                "config": {"mapping": "{{ 1 + }}"},
                "on_error": "continueErrorOutput",
            },
            {"id": "ok", "type": "set", "name": "Happy path", "config": {"values": {"ok": True}}},
            {
                "id": "handler",
                "type": "set",
                "name": "Error handler",
                "config": {"values": {"handled": "{{ $json.error }}"}},
            },
        ],
        [
            {"id": "e1", "from": "t", "to": "boom"},
            {"id": "e2", "from": "boom", "to": "ok"},
            {"id": "e3", "from": "boom", "to": "handler", "connection_type": "error"},
        ],
    )


def test_error_output_routes_failure_and_keeps_run_green():
    definition = _error_output_definition()
    assert validate_definition(definition).valid

    runs, _, status = _run(definition)

    boom = _by_id(runs, "boom")
    handler = _by_id(runs, "handler")
    happy = _by_id(runs, "ok")
    assert status == "completed"
    assert boom["status"] == "failed"
    assert boom["handled"] is True
    assert boom["ports"] == ["error"]
    assert handler["status"] == "succeeded"
    assert handler["outputs"]["handled"].startswith("invalid") or handler["outputs"]["handled"]
    assert happy["status"] == "skipped"
    assert happy["reason"] == "BRANCH_NOT_TAKEN"


def test_error_output_requires_the_setting_for_error_edges():
    definition = _error_output_definition()
    definition["nodes"][0].pop("on_error")

    verdict = validate_definition(definition)

    assert not verdict.valid
    assert any(error.code == "CONNECTION_TYPE_UNAVAILABLE" for error in verdict.errors)


def test_error_output_validation_rejects_unknown_mode():
    definition = _error_output_definition()
    definition["nodes"][0]["on_error"] = "explode"

    verdict = validate_definition(definition)

    assert not verdict.valid
    assert any(error.code == "INVALID_ERROR_HANDLING" for error in verdict.errors)


# ---------------------------------------------------------------------------
# alwaysOutputData / executeOnce
# ---------------------------------------------------------------------------


def _filter_definition(**settings):
    return _definition(
        [
            {
                "id": "filt",
                "type": "filter",
                "name": "Keep none",
                "config": {"items": "{{ $json.items }}", "predicate": "{{ false }}"},
                **settings,
            },
            {"id": "after", "type": "set", "name": "After", "config": {"values": {"after": True}}},
        ],
        [
            {"id": "e1", "from": "t", "to": "filt"},
            {"id": "e2", "from": "filt", "to": "after"},
        ],
        triggers=[
            {
                "id": "t",
                "type": "manual",
                "name": "Start",
                "config": {},
            }
        ],
    )


def test_always_output_data_emits_one_empty_item():
    definition = _filter_definition(always_output_data=True)

    runs, _, status = _run(definition, {"items": [1, 2]})

    filt = _by_id(runs, "filt")
    assert status == "completed"
    assert filt["items"] == [{"json": {}}]
    assert _by_id(runs, "after")["status"] == "succeeded"


def test_without_always_output_data_empty_stays_empty():
    runs, _, _ = _run(_filter_definition(), {"items": [1, 2]})

    assert _by_id(runs, "filt")["items"] == []


def test_execute_once_runs_with_a_single_item(monkeypatch):
    seen = {}

    async def fake(node, inputs, ctx, items=None):
        seen["items"] = len(items or [])
        return local_engine.NodeOutcome(status="succeeded", outputs={"ok": True})

    monkeypatch.setattr(local_engine, "_exec_node", fake)
    node = {
        "id": "once",
        "type": "transform",
        "name": "Once",
        "config": {},
        "execute_once": True,
    }
    items = [{"json": {"n": n}} for n in range(3)]

    async def run():
        return await local_engine._execute_with_policy(
            node, {"input": {"items": [1, 2, 3]}}, local_engine.RunContext(), items=items
        )

    outcome, attempts, continued = asyncio.run(run())

    assert outcome.status == "succeeded"
    assert seen["items"] == 1
    assert attempts == 1
    assert continued is False


def test_legacy_max_attempts_alias_still_validates():
    definition = _flaky_definition(retry_policy={"max_attempts": 2})

    assert validate_definition(definition).valid


@pytest.mark.parametrize("bad", [0, 11])
def test_max_tries_range_is_validated(bad):
    definition = _flaky_definition(retry_on_fail=True, max_tries=bad)

    verdict = validate_definition(definition)

    assert not verdict.valid
    assert any(error.code == "INVALID_RETRY_POLICY" for error in verdict.errors)
