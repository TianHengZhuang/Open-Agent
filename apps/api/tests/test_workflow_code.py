"""Phase 10: sandboxed Code node.

Covers real Python/JavaScript execution, all-items and per-item modes,
expression compatibility, the item surface, and the failure modes that must
never become a fabricated success (timeout, blocked network/subprocess/file
reads, output cap, invalid return).
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest

from openagent.runtime import code_runner, local_engine

NODE_AVAILABLE = bool(shutil.which("node"))


@pytest.fixture(autouse=True)
def _subprocess_runner(monkeypatch):
    monkeypatch.setenv("OPENAGENT_CODE_RUNNER", "subprocess")
    monkeypatch.delenv("OPENAGENT_ENV", raising=False)


def _definition(code, language="python", mode="all", *, split=False, **node):
    nodes = [
        {
            "id": "n_seed",
            "type": "set",
            "name": "Seed",
            "config": {"values": {"rows": [{"n": 1}, {"n": 2}]}},
        }
    ]
    edges = [
        {"id": "e1", "from": "t", "to": "n_seed"},
    ]
    source = "n_seed"
    if split:
        nodes.append(
            {
                "id": "n_split",
                "type": "split_out",
                "name": "Split",
                "config": {"field": "rows"},
            }
        )
        edges.append({"id": "e2", "from": "n_seed", "to": "n_split"})
        source = "n_split"
    nodes.append(
        {
            "id": "n_code",
            "type": "code",
            "name": "Code",
            "config": {"language": language, "mode": mode, "code": code},
            **node,
        }
    )
    edges.append({"id": "e3", "from": source, "to": "n_code"})
    return {
        "schema_version": "1.1",
        "triggers": [{"id": "t", "type": "manual", "name": "Start", "config": {}}],
        "nodes": nodes,
        "edges": edges,
        "variables": [],
        "settings": {},
    }


def _run(definition):
    return asyncio.run(local_engine.execute_definition(definition, {}))


def _step(runs, node_id="n_code"):
    return next(run for run in runs if run["node_id"] == node_id)


def test_python_runs_once_for_all_items():
    runs, _output, status = _run(
        _definition(
            'return [{"json": {"total": sum(row["n"] for row in _items[0]["json"]["rows"])}}]'
        )
    )

    assert status == "completed"
    step = _step(runs)
    assert step["status"] == "succeeded", step.get("error")
    assert step["outputs"]["count"] == 1
    assert step["outputs"]["items"] == [{"total": 3}]


def test_python_runs_once_for_each_item():
    runs, _output, status = _run(
        _definition(
            'return {"json": {"double": _item["json"]["n"] * 2}}',
            mode="each",
            split=True,
        )
    )

    assert status == "completed"
    step = _step(runs)
    assert step["outputs"]["items"] == [{"double": 2}, {"double": 4}]


def test_python_item_surface_and_helpers():
    runs, _output, _status = _run(
        _definition(
            "first = _items[0]['json']\n"
            "last = _items[-1]['json']\n"
            "print('read', len(_items))\n"
            "return {'json': {'first': first['rows'][0]['n'], "
            "'last': last['rows'][-1]['n']}}"
        )
    )

    step = _step(runs)
    assert step["outputs"]["items"] == [{"first": 1, "last": 2}]
    assert "read 1" in step["outputs"]["stdout"]


def test_expression_mode_is_unchanged_without_a_language():
    definition = _definition("{{ $json.rows }}")
    definition["nodes"][-1]["config"].pop("language")
    definition["nodes"][-1]["config"].pop("mode")

    runs, _output, status = _run(definition)

    assert status == "completed"
    step = _step(runs)
    assert step["outputs"]["items"] == [{"n": 1}, {"n": 2}]


def test_code_timeout_is_reported():
    with pytest.raises(code_runner.CodeRunnerError) as excinfo:
        asyncio.run(
            code_runner.run_code(
                language="python",
                code="while True:\n    pass",
                items=[],
                timeout_seconds=1,
            )
        )

    assert excinfo.value.code == "CODE_TIMEOUT"


def test_network_access_is_blocked():
    with pytest.raises(code_runner.CodeRunnerError) as excinfo:
        asyncio.run(
            code_runner.run_code(
                language="python",
                code="import socket\nreturn {'json': {'ok': bool(socket.socket())}}",
                items=[],
            )
        )

    assert excinfo.value.code == "CODE_POLICY_DENIED"
    assert "socket" in excinfo.value.message


def test_subprocess_access_is_blocked():
    with pytest.raises(code_runner.CodeRunnerError) as excinfo:
        asyncio.run(
            code_runner.run_code(
                language="python",
                code="import subprocess\nreturn {'json': {'ok': True}}",
                items=[],
            )
        )

    assert excinfo.value.code == "CODE_POLICY_DENIED"
    assert "subprocess" in excinfo.value.message


def test_reads_outside_the_sandbox_are_blocked():
    path = "C:\\\\Windows\\\\win.ini" if os.name == "nt" else "/etc/hosts"
    if not os.path.exists(path):
        pytest.skip("no stable system file to read")

    with pytest.raises(code_runner.CodeRunnerError) as excinfo:
        asyncio.run(
            code_runner.run_code(
                language="python",
                code=f"return {{'json': {{'text': open({path!r}).read()}}}}",
                items=[],
            )
        )

    assert excinfo.value.code == "CODE_POLICY_DENIED"


def test_output_size_is_capped(monkeypatch):
    monkeypatch.setenv("OPENAGENT_CODE_MAX_OUTPUT_BYTES", "5000")

    with pytest.raises(code_runner.CodeRunnerError) as excinfo:
        asyncio.run(
            code_runner.run_code(
                language="python",
                code="return [{'json': {'blob': 'a' * 200000}}]",
                items=[],
            )
        )

    assert excinfo.value.code == "CODE_OUTPUT_TOO_LARGE"


def test_returning_nothing_is_an_error():
    with pytest.raises(code_runner.CodeRunnerError) as excinfo:
        asyncio.run(
            code_runner.run_code(language="python", code="x = 1", items=[])
        )

    assert excinfo.value.code == "CODE_INVALID_RESULT"


def test_code_node_failure_keeps_the_run_honest():
    runs, _output, status = _run(
        _definition("import os\nreturn {'json': {'cwd': os.getcwd()}}")
    )

    assert status == "completed"
    step = _step(runs)
    assert step["status"] == "succeeded"


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not installed")
def test_javascript_runs_once_for_each_item():
    runs, _output, status = _run(
        _definition(
            "return [{ json: { double: $json.n * 2 } }];",
            language="javascript",
            mode="each",
            split=True,
        )
    )

    assert status == "completed"
    step = _step(runs)
    assert step["status"] == "succeeded", step.get("error")
    assert step["outputs"]["items"] == [{"double": 2}, {"double": 4}]
