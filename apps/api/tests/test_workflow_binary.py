"""Phase 8: binary data and file nodes.

Covers the item `binary` contract, the local binary store, convert/extract,
sandboxed file read/write, `$binary`, webhook uploads and binary pass-through.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openagent.runtime import binary_store, local_engine
from openagent.runtime.binary_store import (
    delete_binary,
    make_binary_property,
    read_binary,
    resolve_user_path,
)
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


def _by_id(runs, node_id):
    return next(run for run in runs if run["node_id"] == node_id)


def _rows_definition(*, write_path=None, read_path=None):
    """trigger -> set(list) -> map(items) -> convert -> [write -> read] -> extract."""
    nodes = [
        {
            "id": "seed",
            "type": "set",
            "name": "Seed",
            "config": {"values": {"rows": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]}},
        },
        {
            "id": "rows",
            "type": "map",
            "name": "Rows",
            "config": {"items": "{{ $json.rows }}", "expression": "{{ $json }}"},
        },
        {
            "id": "convert",
            "type": "convert_to_file",
            "name": "To CSV",
            "config": {"format": "csv", "file_name": "rows.csv"},
        },
    ]
    edges = [
        {"id": "e1", "from": "t", "to": "seed"},
        {"id": "e2", "from": "seed", "to": "rows"},
        {"id": "e3", "from": "rows", "to": "convert"},
    ]
    last = "convert"
    if write_path:
        nodes.append(
            {
                "id": "write",
                "type": "write_binary_file",
                "name": "Write",
                "config": {"path": write_path},
            }
        )
        edges.append({"id": "e4", "from": last, "to": "write"})
        last = "write"
    if read_path:
        nodes.append(
            {
                "id": "read",
                "type": "read_binary_file",
                "name": "Read",
                "config": {"path": read_path},
            }
        )
        edges.append({"id": "e5", "from": last, "to": "read"})
        last = "read"
    nodes.append(
        {
            "id": "extract",
            "type": "extract_from_file",
            "name": "Parse",
            "config": {"format": "auto"},
        }
    )
    edges.append({"id": "e6", "from": last, "to": "extract"})
    return _definition(nodes, edges)


def test_convert_and_extract_csv_round_trip():
    definition = _rows_definition()
    assert validate_definition(definition).valid

    runs, _, status = _run(definition)

    convert = _by_id(runs, "convert")
    extract = _by_id(runs, "extract")
    assert status == "completed"
    assert convert["items"][0]["binary"]["data"]["fileName"] == "rows.csv"
    assert convert["items"][0]["binary"]["data"]["mimeType"] == "text/csv"
    assert [item["json"] for item in extract["items"]] == [
        {"id": "1", "name": "Ada"},
        {"id": "2", "name": "Grace"},
    ]


def test_write_and_read_binary_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAGENT_FILE_DIR", str(tmp_path))

    runs, _, status = _run(_rows_definition(write_path="out/rows.csv", read_path="out/rows.csv"))

    write = _by_id(runs, "write")
    read = _by_id(runs, "read")
    extract = _by_id(runs, "extract")
    assert status == "completed"
    assert (tmp_path / "out" / "rows.csv").exists()
    assert write["outputs"]["fileSize"] > 0
    assert read["items"][0]["binary"]["data"]["fileName"] == "rows.csv"
    assert len(extract["items"]) == 2


def test_file_nodes_reject_parent_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAGENT_FILE_DIR", str(tmp_path / "root"))
    with pytest.raises(ValueError):
        resolve_user_path("../escape.txt")

    definition = _definition(
        [
            {
                "id": "write",
                "type": "write_binary_file",
                "name": "Write",
                "config": {"path": "../escape.txt"},
            }
        ],
        [{"id": "e1", "from": "t", "to": "write"}],
    )
    runs, _, status = _run(definition)

    assert status == "failed"
    assert _by_id(runs, "write")["error_code"] == "INVALID_PATH"


def test_binary_store_round_trip_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAGENT_BINARY_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAGENT_BINARY_INLINE_BYTES", "0")

    prop = make_binary_property(b"hello world", file_name="greeting.txt")
    assert "storageId" in prop
    assert prop["fileSize"] == 11
    assert read_binary(prop) == b"hello world"
    assert delete_binary(prop) is True
    with pytest.raises(ValueError):
        read_binary(prop)


def test_binary_store_enforces_the_size_cap(monkeypatch):
    monkeypatch.setenv("OPENAGENT_BINARY_MAX_BYTES", "2048")

    with pytest.raises(ValueError):
        make_binary_property(b"x" * 4096, file_name="big.bin")


def test_binary_expression_reads_the_property():
    definition = _rows_definition()
    # Read the binary metadata right after the file is produced.
    definition["edges"] = [
        edge for edge in definition["edges"] if edge["id"] != "e6"
    ]
    definition["nodes"].append(
        {
            "id": "meta",
            "type": "set",
            "name": "Meta",
            "config": {
                "values": {
                    "name": "{{ $binary.data.fileName }}",
                    "size": "{{ $binary.data.fileSize }}",
                }
            },
        }
    )
    definition["edges"].extend(
        [
            {"id": "e7", "from": "convert", "to": "meta"},
            {"id": "e8", "from": "meta", "to": "extract"},
        ]
    )

    runs, _, status = _run(definition)

    assert status == "completed"
    assert _by_id(runs, "meta")["outputs"]["name"] == "rows.csv"
    assert _by_id(runs, "meta")["outputs"]["size"] > 0
    assert len(_by_id(runs, "extract")["items"]) == 2


def test_binary_survives_a_pass_through_node():
    definition = _rows_definition()
    # Insert a Set between convert and extract: it must not drop the binary.
    definition["edges"] = [
        edge for edge in definition["edges"] if edge["id"] != "e6"
    ]
    definition["nodes"].insert(
        3,
        {
            "id": "tag",
            "type": "set",
            "name": "Tag",
            "config": {"values": {"tagged": True}},
        },
    )
    definition["edges"] = [
        {"id": "e1", "from": "t", "to": "seed"},
        {"id": "e2", "from": "seed", "to": "rows"},
        {"id": "e3", "from": "rows", "to": "convert"},
        {"id": "e4", "from": "convert", "to": "tag"},
        {"id": "e5", "from": "tag", "to": "extract"},
    ]

    runs, _, status = _run(definition)

    assert status == "completed"
    assert _by_id(runs, "tag")["items"][0]["binary"]["data"]["fileName"] == "rows.csv"
    assert len(_by_id(runs, "extract")["items"]) == 2


def test_webhook_upload_reaches_extract():
    csv_bytes = b"id,name\n7,Linus\n8,Margaret\n"
    prop = make_binary_property(csv_bytes, file_name="upload.csv", mime_type="text/csv")
    definition = _definition(
        [
            {
                "id": "extract",
                "type": "extract_from_file",
                "name": "Parse upload",
                "config": {"format": "auto"},
            }
        ],
        [{"id": "e1", "from": "t", "to": "extract"}],
        triggers=[{"id": "t", "type": "webhook", "name": "Upload", "config": {"path": "upload"}}],
    )

    runs, _, status = _run(definition, {"__binary": {"data": prop}, "body": {}})

    assert status == "completed"
    assert [item["json"] for item in _by_id(runs, "extract")["items"]] == [
        {"id": "7", "name": "Linus"},
        {"id": "8", "name": "Margaret"},
    ]


def test_extract_without_binary_fails_cleanly():
    definition = _definition(
        [{"id": "extract", "type": "extract_from_file", "name": "Parse", "config": {}}],
        [{"id": "e1", "from": "t", "to": "extract"}],
    )

    runs, _, status = _run(definition, {"hello": "world"})

    assert status == "failed"
    assert _by_id(runs, "extract")["error_code"] == "NO_BINARY_INPUT"


def test_json_binary_round_trip():
    prop = make_binary_property(
        json.dumps([{"a": 1}, {"a": 2}]).encode(), file_name="items.json", mime_type="application/json"
    )
    definition = _definition(
        [
            {
                "id": "setb",
                "type": "set",
                "name": "Binary in",
                "config": {},
            },
            {"id": "extract", "type": "extract_from_file", "name": "Parse", "config": {}},
        ],
        [
            {"id": "e1", "from": "t", "to": "setb"},
            {"id": "e2", "from": "setb", "to": "extract"},
        ],
        triggers=[
            {"id": "t", "type": "webhook", "name": "Upload", "config": {"path": "upload"}}
        ],
    )

    runs, _, status = _run(definition, {"__binary": {"data": prop}})

    assert status == "completed"
    assert [item["json"] for item in _by_id(runs, "extract")["items"]] == [{"a": 1}, {"a": 2}]


def test_prune_binaries_removes_old_files(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("OPENAGENT_BINARY_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAGENT_BINARY_INLINE_BYTES", "0")

    prop = make_binary_property(b"old", file_name="old.txt")
    path = tmp_path / prop["storageId"]
    old = time.time() - 8 * 24 * 3600
    os.utime(path, (old, old))

    assert binary_store.prune_binaries() == 1
    assert not path.exists()
