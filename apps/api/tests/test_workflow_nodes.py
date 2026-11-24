"""Phase 5 node coverage: edit fields, aggregate, split in batches, code and
respond-to-webhook."""

from __future__ import annotations

import asyncio

import pytest

from openagent.db.models import Workflow, WorkflowStatus, WorkflowVersion
from openagent.runtime import local_engine
from openagent.services.workflow_definition import validate_definition


def _definition(nodes, edges, *, triggers=None):
    return {
        "schema_version": "1.1",
        "triggers": triggers
        or [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": edges,
        "variables": [],
        "settings": {},
    }


def _run(definition, trigger_input=None):
    return asyncio.run(local_engine.execute_definition(definition, trigger_input or {}))


def _rows(items):
    """A seed + map pair that turns a list into one item per entry."""
    return (
        [
            {"id": "seed", "type": "set", "name": "Seed", "config": {"values": {"items": items}}},
            {
                "id": "rows",
                "type": "map",
                "name": "Rows",
                "config": {"items": "{{ $json.items }}", "expression": "{{ $json }}"},
            },
        ],
        [
            {"id": "e1", "from": "t", "to": "seed"},
            {"id": "e2", "from": "seed", "to": "rows"},
        ],
    )


def _by_id(runs, node_id):
    return next(run for run in runs if run["node_id"] == node_id)


# ---------------------------------------------------------------------------
# Edit fields
# ---------------------------------------------------------------------------


def test_edit_fields_adds_keeps_and_removes_fields():
    nodes, edges = _rows(
        [
            {"id": 1, "name": "Ada", "internal": "x", "secret": "y"},
            {"id": 2, "name": "Grace", "internal": "z", "secret": "w"},
        ]
    )
    nodes.append(
        {
            "id": "edit",
            "type": "edit_fields",
            "name": "Tidy",
            "config": {
                "keep_only": "id, name",
                "remove": "secret",
                "fields": {"label": "{{ $json.name }} ({{ $json.id }})"},
            },
        }
    )
    edges.append({"id": "e3", "from": "rows", "to": "edit"})

    runs, _, status = _run(_definition(nodes, edges))
    assert status == "completed"

    edit = _by_id(runs, "edit")
    assert [item["json"] for item in edit["items"]] == [
        {"id": 1, "name": "Ada", "label": "Ada (1)"},
        {"id": 2, "name": "Grace", "label": "Grace (2)"},
    ]
    assert [item["pairedItem"]["item"] for item in edit["items"]] == [0, 1]


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def _aggregate(mode, **config):
    nodes, edges = _rows(
        [
            {"id": 1, "customer": "a", "amount": 10},
            {"id": 2, "customer": "b", "amount": 5},
            {"id": 3, "customer": "a", "amount": 20},
        ]
    )
    nodes.append(
        {
            "id": "agg",
            "type": "aggregate",
            "name": "Rollup",
            "config": {"mode": mode, **config},
        }
    )
    edges.append({"id": "e3", "from": "rows", "to": "agg"})
    runs, _, status = _run(_definition(nodes, edges))
    assert status == "completed"
    return _by_id(runs, "agg")


def test_aggregate_collects_counts_sums_and_averages():
    assert _aggregate("collect", field="amount")["outputs"] == {"amount": [10, 5, 20], "count": 3}
    assert _aggregate("count")["outputs"] == {"count": 3, "items": [{"count": 3}]}
    assert _aggregate("sum", field="amount")["outputs"] == {"sum": 35.0, "count": 3, "field": "amount"}
    assert _aggregate("average", field="amount")["outputs"] == {
        "average": 35.0 / 3,
        "count": 3,
        "field": "amount",
    }


def test_aggregate_groups_by_a_field():
    agg = _aggregate("group_by", group_by="customer")
    groups = {group["json"]["key"]: group["json"] for group in agg["items"]}
    assert groups["a"]["count"] == 2
    assert [entry["amount"] for entry in groups["a"]["items"]] == [10, 20]
    assert groups["b"]["count"] == 1


# ---------------------------------------------------------------------------
# Split in batches + code
# ---------------------------------------------------------------------------


def test_split_in_batches_chunks_items():
    nodes, edges = _rows([{"id": index} for index in range(5)])
    nodes.append(
        {
            "id": "batch",
            "type": "split_in_batches",
            "name": "Batches",
            "config": {"batch_size": 2},
        }
    )
    edges.append({"id": "e3", "from": "rows", "to": "batch"})

    runs, _, status = _run(_definition(nodes, edges))
    assert status == "completed"

    batch = _by_id(runs, "batch")
    assert [item["json"]["size"] for item in batch["items"]] == [2, 2, 1]
    assert batch["items"][0]["json"]["batch"] == [{"id": 0}, {"id": 1}]
    assert batch["items"][2]["json"]["total"] == 5


def test_code_evaluates_an_expression_into_items_or_one_item():
    nodes, edges = _rows([{"id": 1}, {"id": 2}])
    nodes.append(
        {
            "id": "code",
            "type": "code",
            "name": "IDs",
            "config": {"code": "{{ $input.all().map(i => i.json.id) }}"},
        }
    )
    edges.append({"id": "e3", "from": "rows", "to": "code"})

    runs, _, status = _run(_definition(nodes, edges))
    assert status == "completed"
    assert [item["json"] for item in _by_id(runs, "code")["items"]] == [1, 2]

    nodes[-1] = {
        "id": "code",
        "type": "code",
        "name": "Summary",
        "config": {"code": {"total": "{{ $input.all().length }}"}},
    }
    runs, _, status = _run(_definition(nodes, edges))
    assert status == "completed"
    assert _by_id(runs, "code")["outputs"] == {"total": 2}


def test_respond_to_webhook_records_the_response():
    definition = _definition(
        [
            {
                "id": "r",
                "type": "respond_to_webhook",
                "name": "Respond",
                "config": {"status": 201, "body": {"ok": True}, "headers": {"X-Test": "1"}},
            }
        ],
        [{"id": "e1", "from": "t", "to": "r"}],
    )
    runs, _, status = _run(definition)
    assert status == "completed"
    respond = _by_id(runs, "r")
    assert respond["outputs"]["responded"] is True
    assert respond["outputs"]["status"] == 201
    assert respond["outputs"]["body"] == {"ok": True}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_new_node_config_is_validated():
    base = _definition(
        [{"id": "n", "type": "set", "name": "N", "config": {"values": {"a": 1}}}],
        [{"id": "e1", "from": "t", "to": "n"}],
    )

    base["nodes"].append({"id": "a", "type": "aggregate", "name": "A", "config": {"mode": "sum"}})
    base["edges"].append({"id": "e2", "from": "n", "to": "a"})
    verdict = validate_definition(base)
    assert not verdict.valid
    assert any(error.field == "nodes[1].config.field" for error in verdict.errors)

    base["nodes"][1]["config"] = {"mode": "group_by"}
    verdict = validate_definition(base)
    assert not verdict.valid

    base["nodes"][1] = {"id": "a", "type": "split_in_batches", "name": "B", "config": {"batch_size": 0}}
    verdict = validate_definition(base)
    assert not verdict.valid

    base["nodes"][1] = {"id": "a", "type": "code", "name": "C", "config": {}}
    verdict = validate_definition(base)
    assert any(error.field == "nodes[1].config.code" for error in verdict.errors)

    base["nodes"][1] = {
        "id": "a",
        "type": "respond_to_webhook",
        "name": "R",
        "config": {"status": 900},
    }
    verdict = validate_definition(base)
    assert any(error.field == "nodes[1].config.status" for error in verdict.errors)


# ---------------------------------------------------------------------------
# HTTP: respond to webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_returns_the_responder_payload(client, db_session, org_a):
    definition = {
        "schema_version": "1.1",
        "triggers": [
            {"id": "t", "type": "webhook", "name": "Hook", "config": {"path": "phase5-respond"}}
        ],
        "nodes": [
            {
                "id": "r",
                "type": "respond_to_webhook",
                "name": "Respond",
                "config": {
                    "status": 201,
                    "body": {"ok": True, "echo": "{{ $json.body.message }}"},
                    "headers": {"X-Phase": "5"},
                },
            }
        ],
        "edges": [{"id": "e1", "from": "t", "to": "r"}],
        "variables": [],
        "settings": {},
    }
    workflow = Workflow(
        organization_id=org_a.id,
        name="Responder",
        slug="responder",
        status=WorkflowStatus.ACTIVE,
    )
    db_session.add(workflow)
    await db_session.flush()
    db_session.add(
        WorkflowVersion(
            workflow_id=workflow.id,
            version="1.0.0",
            schema_version="1.1",
            definition=definition,
        )
    )
    await db_session.commit()

    response = await client.post("/api/v1/hooks/phase5-respond", json={"message": "hi"})
    assert response.status_code == 201, response.text
    assert response.json() == {"ok": True, "echo": "hi"}
    assert response.headers["x-phase"] == "5"
