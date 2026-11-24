"""Phase 11: node coverage tiers and the Tier A local core nodes.

Every Tier A node has a real executor here; the coverage map and the generated
registry must agree in both directions, and the coverage answer must never
present a Tier C capability as a built-in node.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib

import pytest

from openagent.api.dependencies import get_current_org_context
from openagent.api.v1 import nodes as nodes_api
from openagent.main import app
from openagent.registry import coverage, node_registry
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


def _run_chain(node, upstream=()):
    nodes = [*upstream, node]
    edges = []
    previous = "t"
    for index, entry in enumerate(nodes, start=1):
        edges.append({"id": f"e{index}", "from": previous, "to": entry["id"]})
        previous = entry["id"]
    runs, _output, status = asyncio.run(
        local_engine.execute_definition(_definition(nodes, edges), {})
    )
    run = next(entry for entry in runs if entry["node_id"] == node["id"])
    return run, status


def _node(node_type, config=None, node_id="n1", **extra):
    return {
        "id": node_id,
        "type": node_type,
        "name": node_type.replace("_", " ").title(),
        "config": config or {},
        **extra,
    }


# ---------------------------------------------------------------------------
# Tier A executors
# ---------------------------------------------------------------------------


def test_crypto_hash_and_base64_roundtrip():
    run, _ = _run_chain(
        _node("crypto", {"action": "hash", "algorithm": "sha256", "value": "hello"})
    )
    assert run["status"] == "succeeded"
    assert run["outputs"]["value"] == hashlib.sha256(b"hello").hexdigest()

    encoded = base64.b64encode(b"secret value").decode("ascii")
    run, _ = _run_chain(_node("crypto", {"action": "base64_decode", "value": encoded}))
    assert run["outputs"]["value"] == "secret value"

    run, _ = _run_chain(_node("crypto", {"action": "uuid"}))
    assert len(run["outputs"]["value"]) == 36


def test_crypto_unknown_algorithm_fails_loudly():
    run, status = _run_chain(_node("crypto", {"action": "hash", "algorithm": "rot13", "value": "x"}))
    assert run["status"] == "failed"
    assert status == "failed"


def test_datetime_shift_and_diff():
    run, _ = _run_chain(
        _node(
            "datetime",
            {
                "action": "shift",
                "date": "2024-01-31",
                "amount": 1,
                "unit": "months",
                "format": "yyyy-MM-dd",
            },
        )
    )
    assert run["status"] == "succeeded"
    assert run["outputs"]["value"] == "2024-02-28"

    run, _ = _run_chain(
        _node("datetime", {"action": "diff", "date": "2024-01-01", "to": "2024-01-02"})
    )
    assert run["outputs"]["seconds"] == 86400


def test_datetime_rejects_unparseable_input():
    run, _ = _run_chain(_node("datetime", {"action": "parse", "date": "not a date"}))
    assert run["status"] == "failed"
    assert run["error_code"] == "INVALID_INPUT"


def test_html_extract_text_links_and_table():
    run, _ = _run_chain(
        _node(
            "html",
            {
                "action": "extract_text",
                "html": "<p>Hello <b>world</b></p><script>bad()</script>",
            },
        )
    )
    assert run["outputs"]["text"] == "Hello world"

    run, _ = _run_chain(
        _node("html", {"action": "extract_links", "html": '<a href="/a">A</a><a href="/b">B</a>'})
    )
    assert run["outputs"]["links"] == ["/a", "/b"]

    seed = _node("set", {"values": {"rows": [{"id": 1, "name": "Ada"}]}}, node_id="seed")
    rows = _node(
        "map",
        {"items": "{{ $json.rows }}", "expression": "{{ $json }}"},
        node_id="rows",
    )
    table = _node("html", {"action": "build_table"}, node_id="table")
    definition = _definition(
        [seed, rows, table],
        [
            {"id": "e1", "from": "t", "to": "seed"},
            {"id": "e2", "from": "seed", "to": "rows"},
            {"id": "e3", "from": "rows", "to": "table"},
        ],
    )
    runs, _output, status = asyncio.run(local_engine.execute_definition(definition, {}))
    run = next(entry for entry in runs if entry["node_id"] == "table")
    assert status == "completed"
    assert "<td>Ada</td>" in run["outputs"]["html"]


def test_xml_parse_and_build_roundtrip():
    run, _ = _run_chain(
        _node("xml", {"action": "parse", "xml": '<order id="7"><item>Pen</item></order>'})
    )
    assert run["status"] == "succeeded"
    assert run["outputs"]["data"] == {
        "order": {"@id": "7", "item": "Pen"},
    }

    run, _ = _run_chain(
        _node(
            "xml",
            {"action": "build", "root": "order", "data": {"id": "7", "item": "Pen"}},
        )
    )
    assert run["outputs"]["xml"] == "<order><id>7</id><item>Pen</item></order>"


def test_xml_invalid_document_fails():
    run, _ = _run_chain(_node("xml", {"action": "parse", "xml": "<open>"}))
    assert run["status"] == "failed"
    assert run["error_code"] == "INVALID_INPUT"


def test_compression_gzip_roundtrip():
    run, _ = _run_chain(_node("compression", {"action": "gzip", "data": "hello world"}))
    assert run["status"] == "succeeded"
    payload = base64.b64decode(run["outputs"]["data"])
    assert gzip.decompress(payload) == b"hello world"

    run, _ = _run_chain(
        _node("compression", {"action": "gunzip", "data": run["outputs"]["data"]})
    )
    assert run["outputs"]["text"] == "hello world"

    run, _ = _run_chain(_node("compression", {"action": "zip", "data": "row,value"}))
    zipped = run["outputs"]["data"]
    run, _ = _run_chain(_node("compression", {"action": "unzip", "data": zipped}))
    assert base64.b64decode(run["outputs"]["data"]) == b"row,value"


def test_compare_datasets_splits_all_branches():
    seed_a = _node("set", {"values": {"rows": [{"id": 1}, {"id": 2}]}}, node_id="seed_a")
    rows_a = _node("map", {"items": "{{ $json.rows }}", "expression": "{{ $json }}"}, node_id="rows_a")
    seed_b = _node("set", {"values": {"rows": [{"id": 2}, {"id": 3}]}}, node_id="seed_b")
    rows_b = _node("map", {"items": "{{ $json.rows }}", "expression": "{{ $json }}"}, node_id="rows_b")
    compare = _node("compare_datasets", {"field": "id"}, node_id="compare")
    definition = _definition(
        [seed_a, rows_a, seed_b, rows_b, compare],
        [
            {"id": "e1", "from": "t", "to": "seed_a"},
            {"id": "e2", "from": "seed_a", "to": "rows_a"},
            {"id": "e3", "from": "t", "to": "seed_b"},
            {"id": "e4", "from": "seed_b", "to": "rows_b"},
            {"id": "e5", "from": "rows_a", "to": "compare"},
            {"id": "e6", "from": "rows_b", "to": "compare"},
        ],
    )
    runs, _output, status = asyncio.run(local_engine.execute_definition(definition, {}))
    run = next(entry for entry in runs if entry["node_id"] == "compare")
    assert status == "completed"
    counts = run["outputs"]["counts"]
    assert counts["same"] == 1
    assert counts["only_in_a"] == 1
    assert counts["only_in_b"] == 1
    assert counts["different"] == 0


def test_no_op_passes_items_through():
    seed = _node("set", {"values": {"rows": [{"id": 1}]}}, node_id="seed")
    rows = _node("map", {"items": "{{ $json.rows }}", "expression": "{{ $json }}"}, node_id="rows")
    noop = _node("no_op", node_id="noop")
    definition = _definition(
        [seed, rows, noop],
        [
            {"id": "e1", "from": "t", "to": "seed"},
            {"id": "e2", "from": "seed", "to": "rows"},
            {"id": "e3", "from": "rows", "to": "noop"},
        ],
    )
    runs, _output, _status = asyncio.run(local_engine.execute_definition(definition, {}))
    run = next(entry for entry in runs if entry["node_id"] == "noop")
    assert run["status"] == "succeeded"
    assert [item["json"] for item in run["items"]] == [{"id": 1}]


def test_stop_and_error_fails_the_run_with_its_message():
    run, status = _run_chain(
        _node("stop_and_error", {"mode": "error", "message": "Order 7 is invalid"})
    )
    assert run["status"] == "failed"
    assert run["error_code"] == "STOP_AND_ERROR"
    assert run["error"] == "Order 7 is invalid"
    assert status == "failed"


def test_new_nodes_validate_and_keep_ports():
    for node_type in coverage._TIER_A:
        definition = _definition([_node(node_type)], [{"id": "e1", "from": "t", "to": "n1"}])
        result = validate_definition(definition)
        unknown = [issue for issue in result.errors if issue.code == "UNKNOWN_NODE_TYPE"]
        assert not unknown, f"{node_type} is not in the registry: {unknown}"


# ---------------------------------------------------------------------------
# Coverage map
# ---------------------------------------------------------------------------


def test_every_registry_node_has_exactly_one_tier():
    registry = node_registry.load_registry()
    assert coverage.unmapped_types(registry.latest) == []
    assert coverage.unknown_types() == []


def test_tier_a_nodes_are_all_local_and_implemented():
    document = coverage.build_coverage()
    tier_a = [row for row in document["nodes"] if row["tier"] == coverage.TIER_A]
    assert len(tier_a) >= 30
    for row in tier_a:
        assert row["status"] == coverage.STATUS_IMPLEMENTED, row
        assert row["execution"] == "local", row
        assert row["n8n"], row


def test_coverage_connectors_match_registered_providers():
    from openagent.connectors.providers import provider_ids, register_official

    if not provider_ids():
        register_official()
    assert set(provider_ids()) == set(coverage.CONNECTOR_NODES)

    document = coverage.build_coverage()
    assert all(row["available"] for row in document["connectors"])


def test_coverage_answer_is_honest_about_tiers():
    slack = coverage.answer("slack")
    assert "connector_action" in slack["answer"]
    assert "built in" not in slack["answer"].split("n8n:")[0].lower()

    airtable = coverage.answer("airtable")
    assert "not built in" in airtable["answer"]
    assert "http request" in airtable["answer"].lower()

    unknown = coverage.answer("teleportation")
    assert "could not match" in unknown["answer"]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def coverage_auth(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(nodes_api, "require_permission", lambda permission: _allow)
    app.dependency_overrides[get_current_org_context] = lambda: None
    yield
    app.dependency_overrides.pop(get_current_org_context, None)


@pytest.mark.asyncio
async def test_coverage_endpoint_matches_the_registry(client, coverage_auth):
    org = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/organizations/{org}/nodes/coverage")
    assert response.status_code == 200
    payload = response.json()
    registry = node_registry.load_registry()
    assert payload["summary"]["nodes"] == len(registry.latest)
    assert payload["summary"]["unmapped"] == 0
    assert {row["type"] for row in payload["nodes"]} == set(registry.latest)
    assert payload["connectors"] and payload["documented"]

    tier_a = await client.get(f"/api/v1/organizations/{org}/nodes/coverage?tier=A")
    assert tier_a.status_code == 200
    assert all(row["tier"] == "A" for row in tier_a.json()["nodes"])

    filtered = await client.get(f"/api/v1/organizations/{org}/nodes/coverage?q=crypto")
    assert [row["type"] for row in filtered.json()["nodes"]] == ["crypto"]


@pytest.mark.asyncio
async def test_coverage_endpoint_requires_authentication(client):
    response = await client.get(
        "/api/v1/organizations/00000000-0000-0000-0000-000000000000/nodes/coverage"
    )
    assert response.status_code == 401
