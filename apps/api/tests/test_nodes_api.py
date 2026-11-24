"""Integration tests for the canonical node registry API."""

import pytest

from openagent.api.dependencies import get_current_org_context
from openagent.api.v1 import nodes as nodes_api
from openagent.main import app


@pytest.fixture
def registry_auth(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(nodes_api, "require_permission", lambda permission: _allow)
    app.dependency_overrides[get_current_org_context] = lambda: None
    yield
    app.dependency_overrides.pop(get_current_org_context, None)


@pytest.mark.asyncio
async def test_registry_requires_authentication(client):
    response = await client.get(
        "/api/v1/organizations/00000000-0000-0000-0000-000000000000/nodes/registry"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_registry_returns_shared_definitions(client, registry_auth):
    org = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/organizations/{org}/nodes/registry")
    assert response.status_code == 200
    payload = response.json()
    assert payload["registry_version"]
    assert len(payload["nodes"]) == 59
    assert len(payload["triggers"]) == 6
    types = {n["type"] for n in payload["nodes"]}
    assert {"condition", "browser_agent", "connector_action"} <= types


@pytest.mark.asyncio
async def test_single_node_definition_and_unknown_type(client, registry_auth):
    org = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/organizations/{org}/nodes/registry/condition")
    assert response.status_code == 200
    node = response.json()
    assert node["type"] == "condition" and node["version"] == 1
    assert any(f["key"] == "expression" for f in node["config_fields"])
    assert [p["name"] for p in node["output_ports"]] == ["true", "false"]
    assert node["input_ports"][0]["name"] == "in"
    assert node["input_ports"][0]["multiple"] is False

    missing = await client.get(f"/api/v1/organizations/{org}/nodes/registry/teleport")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NODE_TYPE_NOT_FOUND"


@pytest.mark.asyncio
async def test_parameter_validation_reports_registry_issues(client, registry_auth):
    org = "00000000-0000-0000-0000-000000000000"
    url = f"/api/v1/organizations/{org}/nodes/registry/prompt/validate"

    bad = await client.post(url, json={"config": {"prompt": "Hi"}})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False
    assert any(i["field"] == "config.model" for i in bad.json()["issues"])

    good = await client.post(
        url, json={"config": {"prompt": "Hi", "model": "gpt-4o-mini"}}
    )
    assert good.status_code == 200
    assert good.json()["valid"] is True

    unknown = await client.post(
        f"/api/v1/organizations/{org}/nodes/registry/teleport/validate", json={"config": {}}
    )
    assert unknown.status_code == 404
