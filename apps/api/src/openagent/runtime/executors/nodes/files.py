"""File and binary node executors (Phase 8).

Four local nodes that make binary data usable end to end:

* ``convert_to_file`` — items -> CSV/JSON/text binary.
* ``extract_from_file`` — binary -> items (CSV/JSON/text).
* ``read_binary_file`` — sandboxed file -> binary item.
* ``write_binary_file`` — binary item -> sandboxed file.

Reading binary from an item never trusts arbitrary paths: the file nodes are
confined to ``OPENAGENT_FILE_DIR`` and every property is size-capped by the
binary store.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

from openagent.runtime.binary_store import (
    make_binary_property,
    max_binary_bytes,
    read_binary,
    resolve_user_path,
)
from openagent.runtime.items import as_items, item_binary, item_json, make_item

FILE_NODE_TYPES = (
    "convert_to_file",
    "extract_from_file",
    "read_binary_file",
    "write_binary_file",
)


def _outcome(status: str, **kwargs: Any):
    from openagent.runtime.local_engine import NodeOutcome

    return NodeOutcome(status=status, **kwargs)


def _source_items(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any) -> List[Dict[str, Any]]:
    from openagent.runtime.local_engine import resolve

    raw = (node.get("config") or {}).get("items")
    if raw:
        return as_items(resolve(raw, ctx))
    if ctx.input_items:
        return list(ctx.input_items)
    collected: List[Any] = []
    for value in inputs.values():
        collected.extend(as_items(value))
    return collected


def _binary_property_name(node: Dict[str, Any]) -> str:
    from openagent.runtime.local_engine import _node_setting

    name = str(_node_setting(node, "binary_property", "") or "").strip()
    return name or "data"


def _pick_binary(items: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for item in items:
        props = item_binary(item)
        if name in props and isinstance(props[name], dict):
            return props[name]
    # Fall back to the first binary property (n8n reads binary.data by default,
    # but a producer may have used another key).
    for item in items:
        for prop in item_binary(item).values():
            if isinstance(prop, dict):
                return prop
    return None


def _csv_text(rows: List[Any], delimiter: str, header: bool) -> str:
    if not rows:
        return ""
    if all(isinstance(row, dict) for row in rows):
        fieldnames: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(str(key))
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=fieldnames, delimiter=delimiter, extrasaction="ignore"
        )
        if header:
            writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value, (dict, list))
                    else ("" if value is None else value)
                )
                for key, value in row.items()
            })
        return buffer.getvalue()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter)
    for row in rows:
        writer.writerow(row if isinstance(row, list) else [row])
    return buffer.getvalue()


def _convert_to_file(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    from openagent.runtime.local_engine import _node_setting, resolve

    items = _source_items(node, inputs, ctx)
    rows = [item_json(item) for item in items]
    fmt = str(_node_setting(node, "format", "csv") or "csv").lower()
    delimiter = str(_node_setting(node, "delimiter", ",") or ",")[:1] or ","
    header = bool(_node_setting(node, "header", True))

    if fmt == "json":
        content = json.dumps(
            rows[0] if len(rows) == 1 else rows, ensure_ascii=False, indent=2
        ).encode("utf-8")
        default_name, mime = "output.json", "application/json"
    elif fmt == "text":
        content = "\n".join(
            row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)
            for row in rows
        ).encode("utf-8")
        default_name, mime = "output.txt", "text/plain"
    elif fmt == "csv":
        content = _csv_text(rows, delimiter, header).encode("utf-8")
        default_name, mime = "output.csv", "text/csv"
    else:
        return _outcome(
            status="failed",
            error=f"Unsupported file format {fmt!r} (csv, json or text)",
            error_code="INVALID_CONFIG",
        )

    raw_name = _node_setting(node, "file_name")
    file_name = str(resolve(raw_name, ctx) or "").strip() or default_name
    try:
        prop = make_binary_property(content, file_name=file_name, mime_type=mime)
    except ValueError as exc:
        return _outcome(status="failed", error=str(exc), error_code="BINARY_TOO_LARGE")

    name = _binary_property_name(node)
    meta = {
        "fileName": prop["fileName"],
        "mimeType": prop["mimeType"],
        "fileSize": prop["fileSize"],
    }
    return _outcome(
        status="succeeded",
        outputs={**meta, "items": [meta], "count": 1},
        items=[make_item(meta, binary={name: prop})],
    )


def _parse_csv(text: str, delimiter: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [dict(row) for row in reader]


def _extract_from_file(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    from openagent.runtime.local_engine import _node_setting

    items = _source_items(node, inputs, ctx)
    prop = _pick_binary(items, _binary_property_name(node))
    if prop is None:
        return _outcome(
            status="failed",
            error="No binary data on the incoming item",
            error_code="NO_BINARY_INPUT",
        )
    try:
        content = read_binary(prop)
    except ValueError as exc:
        return _outcome(status="failed", error=str(exc), error_code="BINARY_UNREADABLE")

    fmt = str(_node_setting(node, "format", "auto") or "auto").lower()
    if fmt == "auto":
        extension = str(prop.get("fileExtension") or "").lower()
        mime = str(prop.get("mimeType") or "").lower()
        if extension == "csv" or "csv" in mime:
            fmt = "csv"
        elif extension == "json" or "json" in mime:
            fmt = "json"
        else:
            fmt = "text"

    delimiter = str(_node_setting(node, "delimiter", ",") or ",")[:1] or ","
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _outcome(
            status="failed",
            error="Binary data is not UTF-8 text; use a decoding node first",
            error_code="BINARY_ENCODING",
        )

    rows: List[Any]
    try:
        if fmt == "csv":
            rows = _parse_csv(text, delimiter)
        elif fmt == "json":
            parsed = json.loads(text)
            rows = parsed if isinstance(parsed, list) else [parsed]
        elif fmt == "text":
            rows = [line for line in text.splitlines()]
        else:
            return _outcome(
                status="failed",
                error=f"Unsupported file format {fmt!r} (csv, json or text)",
                error_code="INVALID_CONFIG",
            )
    except (ValueError, csv.Error) as exc:
        return _outcome(
            status="failed", error=f"Could not read the file: {exc}", error_code="INVALID_BINARY"
        )

    out_items = [make_item(row, index) for index, row in enumerate(rows)]
    return _outcome(
        status="succeeded",
        outputs={"items": rows, "count": len(rows)},
        items=out_items,
    )


def _read_binary_file(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    from openagent.runtime.local_engine import _node_setting, resolve

    raw_path = _node_setting(node, "path")
    path_value = str(resolve(raw_path, ctx) or "").strip() if raw_path else ""
    if not path_value:
        return _outcome(status="failed", error="A file path is required", error_code="INVALID_CONFIG")
    try:
        target = resolve_user_path(path_value)
        if not target.is_file():
            return _outcome(status="failed", error=f"File {path_value!r} was not found", error_code="FILE_NOT_FOUND")
        if target.stat().st_size > max_binary_bytes():
            return _outcome(status="failed", error="File is larger than the binary limit", error_code="BINARY_TOO_LARGE")
        content = target.read_bytes()
    except ValueError as exc:
        return _outcome(status="failed", error=str(exc), error_code="INVALID_PATH")
    except OSError as exc:
        return _outcome(status="failed", error=str(exc), error_code="FILE_ERROR")

    prop = make_binary_property(content, file_name=target.name)
    name = _binary_property_name(node)
    meta = {"fileName": prop["fileName"], "fileSize": prop["fileSize"], "mimeType": prop["mimeType"]}
    return _outcome(
        status="succeeded",
        outputs={**meta, "items": [meta], "count": 1},
        items=[make_item(meta, binary={name: prop})],
    )


def _write_binary_file(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    from openagent.runtime.local_engine import _node_setting, resolve

    raw_path = _node_setting(node, "path")
    path_value = str(resolve(raw_path, ctx) or "").strip() if raw_path else ""
    if not path_value:
        return _outcome(status="failed", error="A file path is required", error_code="INVALID_CONFIG")
    # Validate the path before touching the input, so a bad path is reported as
    # a path error rather than surfacing as "no binary input".
    try:
        target = resolve_user_path(path_value)
    except ValueError as exc:
        return _outcome(status="failed", error=str(exc), error_code="INVALID_PATH")
    items = _source_items(node, inputs, ctx)
    prop = _pick_binary(items, _binary_property_name(node))
    if prop is None:
        return _outcome(
            status="failed", error="No binary data on the incoming item", error_code="NO_BINARY_INPUT"
        )
    try:
        content = read_binary(prop)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    except ValueError as exc:
        return _outcome(status="failed", error=str(exc), error_code="INVALID_PATH")
    except OSError as exc:
        return _outcome(status="failed", error=str(exc), error_code="FILE_ERROR")

    result = {"path": path_value, "fileSize": len(content), "mimeType": prop.get("mimeType")}
    return _outcome(
        status="succeeded",
        outputs={**result, "items": [result], "count": 1},
        items=[make_item(result)],
    )


_EXECUTORS = {
    "convert_to_file": _convert_to_file,
    "extract_from_file": _extract_from_file,
    "read_binary_file": _read_binary_file,
    "write_binary_file": _write_binary_file,
}


async def execute_file_node(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    executor = _EXECUTORS.get(str(node.get("type") or ""))
    if executor is None:
        return _outcome(
            status="failed",
            error=f"No executor for {node.get('type')!r}",
            error_code="NO_EXECUTOR",
        )
    return executor(node, inputs, ctx)
