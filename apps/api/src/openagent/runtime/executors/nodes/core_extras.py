"""Local core node executors (Phase 11, Tier A).

Deterministic, dependency-free nodes that close n8n's local core set:

* ``crypto`` — hashes, HMAC, UUIDs and base64.
* ``datetime`` — parse/format/shift/diff dates.
* ``html`` — extract text/links, build a table.
* ``xml`` — parse XML into JSON and build XML from JSON.
* ``compression`` — gzip/deflate/zip, base64 in and out.
* ``compare_datasets`` — the four n8n Compare Datasets branches.
* ``no_op`` — pass items through untouched.
* ``stop_and_error`` — fail the run with a chosen message.

Everything here is local and synchronous: no network, no credentials, no
fake success. Provider-backed work stays in the connector nodes.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import hmac
import html as html_module
import io
import json
import uuid
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

CORE_EXTRA_NODE_TYPES = (
    "crypto",
    "datetime",
    "html",
    "xml",
    "compression",
    "compare_datasets",
    "no_op",
    "stop_and_error",
)

_ALGORITHMS = ("md5", "sha1", "sha256", "sha512", "sha3_256")
_DATE_TOKENS = (
    ("yyyy", "%Y"),
    ("yy", "%y"),
    ("MM", "%m"),
    ("dd", "%d"),
    ("HH", "%H"),
    ("mm", "%M"),
    ("ss", "%S"),
)


def _outcome(status: str, **kwargs: Any):
    from openagent.runtime.local_engine import NodeOutcome

    return NodeOutcome(status=status, **kwargs)


def _fail(message: str, code: str = "INVALID_CONFIG"):
    return _outcome("failed", error=str(message)[:500], error_code=code)


def _cfg(node: Dict[str, Any], key: str, ctx: Any, default: Any = None) -> Any:
    """Resolve one config value (``{{ }}`` expressions included)."""
    from openagent.runtime.local_engine import resolve

    config = node.get("config") or {}
    if key not in config:
        return default
    return resolve(config.get(key), ctx)


def _incoming(ctx: Any, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    if getattr(ctx, "input_items", None):
        return list(ctx.input_items)
    from openagent.runtime.items import as_items

    collected: List[Dict[str, Any]] = []
    for value in inputs.values():
        collected.extend(as_items(value))
    return collected


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _item_payloads(ctx: Any, inputs: Dict[str, Any]) -> List[Any]:
    from openagent.runtime.items import item_json

    return [item_json(item) for item in _incoming(ctx, inputs)]


def _first_text_field(payloads: List[Any], names: tuple[str, ...]) -> str:
    for payload in payloads:
        if isinstance(payload, dict):
            for name in names:
                if isinstance(payload.get(name), str) and payload[name].strip():
                    return payload[name]
        elif isinstance(payload, str) and payload.strip():
            return payload
    return ""


# ---------------------------------------------------------------------------
# crypto
# ---------------------------------------------------------------------------


def _crypto(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    action = str(_cfg(node, "action", ctx, "hash") or "hash").strip().lower()
    value = _cfg(node, "value", ctx)
    algorithm = str(_cfg(node, "algorithm", ctx, "sha256") or "sha256").lower()
    encoding = str(_cfg(node, "encoding", ctx, "hex") or "hex").lower()

    if action == "uuid":
        raw_count = _cfg(node, "count", ctx, 1)
        try:
            count = max(1, min(int(raw_count or 1), 100))
        except (TypeError, ValueError):
            return _fail("'count' must be an integer")
        values = [str(uuid.uuid4()) for _ in range(count)]
        return _outcome(
            "succeeded",
            outputs={"action": action, "value": values[0] if count == 1 else values,
                     "count": count},
        )

    if action in ("base64_encode", "base64_decode"):
        text = _text(value)
        if not text:
            return _fail("Crypto base64 needs a value")
        if action == "base64_encode":
            result = base64.b64encode(text.encode("utf-8")).decode("ascii")
        else:
            try:
                result = base64.b64decode(text, validate=True).decode("utf-8")
            except (binascii.Error, ValueError) as exc:
                return _fail(f"Value is not valid base64: {exc}", "INVALID_INPUT")
        return _outcome(
            "succeeded",
            outputs={"action": action, "value": result, "encoding": "utf-8"},
        )

    if action not in ("hash", "hmac"):
        return _fail(f"Unknown crypto action {action!r}")

    if algorithm not in _ALGORITHMS:
        return _fail(f"Unsupported algorithm {algorithm!r}; use one of {list(_ALGORITHMS)}")
    data = _text(value).encode("utf-8")
    if not data:
        return _fail("Crypto hash/hmac needs a value")

    if action == "hmac":
        secret = _text(_cfg(node, "secret", ctx, ""))
        if not secret:
            return _fail("HMAC needs a secret")
        digest = hmac.new(secret.encode("utf-8"), data, algorithm).digest()
    else:
        digest = hashlib.new(algorithm, data).digest()

    if encoding == "base64":
        result = base64.b64encode(digest).decode("ascii")
    elif encoding == "hex":
        result = digest.hex()
    else:
        return _fail("'encoding' must be 'hex' or 'base64'")

    return _outcome(
        "succeeded",
        outputs={"action": action, "algorithm": algorithm, "encoding": encoding,
                 "value": result},
    )


# ---------------------------------------------------------------------------
# datetime
# ---------------------------------------------------------------------------


def _parse_moment(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Unix seconds (n8n accepts milliseconds too).
        seconds = float(value) / (1000.0 if float(value) > 100_000_000_000 else 1.0)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_moment(int(text))
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
                        "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _strftime_pattern(fmt: str) -> str:
    """Accept strftime or n8n's token style (``yyyy-MM-dd HH:mm:ss``)."""
    if "%" in fmt:
        return fmt
    converted = fmt
    for token, directive in _DATE_TOKENS:
        converted = converted.replace(token, directive)
    return converted


def _shift_moment(moment: datetime, amount: int, unit: str) -> datetime:
    unit = unit.lower().rstrip("s")
    if unit == "second":
        return moment + timedelta(seconds=amount)
    if unit == "minute":
        return moment + timedelta(minutes=amount)
    if unit == "hour":
        return moment + timedelta(hours=amount)
    if unit == "day":
        return moment + timedelta(days=amount)
    if unit == "week":
        return moment + timedelta(weeks=amount)
    months = amount * (12 if unit == "year" else 1)
    if unit not in ("month", "year"):
        raise ValueError(f"Unknown unit {unit!r}")
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, 28)
    while True:  # keep the day of month when it exists in the target month
        try:
            return moment.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1


def _datetime(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    action = str(_cfg(node, "action", ctx, "now") or "now").strip().lower()
    raw = _cfg(node, "date", ctx) or None
    moment = _parse_moment(raw) if raw not in (None, "") else datetime.now(timezone.utc)
    if moment is None:
        return _fail(f"Could not parse date {raw!r}", "INVALID_INPUT")

    if action == "now":
        moment = datetime.now(timezone.utc)
    elif action in ("shift", "add", "subtract"):
        try:
            amount = int(_cfg(node, "amount", ctx, 0) or 0)
        except (TypeError, ValueError):
            return _fail("'amount' must be an integer")
        unit = str(_cfg(node, "unit", ctx, "days") or "days")
        if action == "subtract":
            amount = -amount
        try:
            moment = _shift_moment(moment, amount, unit)
        except ValueError as exc:
            return _fail(str(exc))
    elif action == "diff":
        target_raw = _cfg(node, "to", ctx) or None
        target = _parse_moment(target_raw)
        if target is None:
            return _fail("datetime diff needs a 'to' date", "INVALID_INPUT")
        seconds = (target - moment).total_seconds()
        return _outcome(
            "succeeded",
            outputs={"action": action, "seconds": seconds, "minutes": seconds / 60,
                     "hours": seconds / 3600, "days": seconds / 86400},
        )
    elif action not in ("parse", "format"):
        return _fail(f"Unknown datetime action {action!r}")

    fmt = str(_cfg(node, "format", ctx, "") or "")
    output = moment.isoformat()
    if fmt:
        output = moment.strftime(_strftime_pattern(fmt))
    return _outcome(
        "succeeded",
        outputs={
            "action": action,
            "date": moment.isoformat(),
            "value": output,
            "timestamp": int(moment.timestamp()),
        },
    )


# ---------------------------------------------------------------------------
# html
# ---------------------------------------------------------------------------


class _HTMLTextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.links: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _html(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    action = str(_cfg(node, "action", ctx, "extract_text") or "extract_text").strip().lower()
    payloads = _item_payloads(ctx, inputs)

    if action == "build_table":
        rows: List[Dict[str, Any]] = [row for row in payloads if isinstance(row, dict)]
        fields: List[str] = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(str(key))
        buffer = io.StringIO()
        buffer.write("<table>\n")
        if fields:
            buffer.write("  <thead><tr>")
            buffer.write("".join(f"<th>{html_module.escape(field)}</th>" for field in fields))
            buffer.write("</tr></thead>\n")
        buffer.write("  <tbody>\n")
        for row in rows:
            buffer.write("    <tr>")
            for field in fields:
                value = row.get(field)
                cell = "" if value is None else (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list)) else str(value)
                )
                buffer.write(f"<td>{html_module.escape(cell)}</td>")
            buffer.write("</tr>\n")
        buffer.write("  </tbody>\n</table>\n")
        markup = buffer.getvalue()
        return _outcome("succeeded", outputs={"html": markup, "rows": len(rows),
                                              "fields": len(fields)})

    source = _cfg(node, "html", ctx) or _first_text_field(payloads, ("html", "body", "data"))
    if not source:
        return _fail("HTML node needs an 'html' value", "INVALID_INPUT")
    parser = _HTMLTextCollector()
    parser.feed(str(source))
    parser.close()
    if action == "extract_text":
        text = " ".join(" ".join(parser.parts).split())
        return _outcome("succeeded", outputs={"text": text, "length": len(text)})
    if action == "extract_links":
        links = list(dict.fromkeys(parser.links))
        return _outcome("succeeded", outputs={"links": links, "count": len(links)})
    return _fail(f"Unknown html action {action!r}")


# ---------------------------------------------------------------------------
# xml
# ---------------------------------------------------------------------------


def _xml_value(element: ElementTree.Element) -> Any:
    children = list(element)
    result: Dict[str, Any] = {f"@{key}": value for key, value in element.attrib.items()}
    text = (element.text or "").strip()
    if children:
        for child in children:
            value = _xml_value(child)
            existing = result.get(child.tag)
            if existing is None:
                result[child.tag] = value
            elif isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        if text:
            result["#text"] = text
        return result
    if result:
        if text:
            result["#text"] = text
        return result
    return text


def _xml_to_json(source: str) -> Any:
    root = ElementTree.fromstring(source)
    return {root.tag: _xml_value(root)}


def _json_to_xml(name: str, value: Any, parent: ElementTree.Element) -> None:
    tag = str(name or "item")
    if not tag.replace("_", "").replace("-", "").replace(".", "").replace(":", "").isalnum():
        tag = "item"
    element = ElementTree.SubElement(parent, tag)
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text.startswith("@"):
                element.set(key_text[1:], _text(child))
            elif key_text == "#text":
                element.text = _text(child)
            elif isinstance(child, list):
                for entry in child:
                    _json_to_xml(key_text, entry, element)
            else:
                _json_to_xml(key_text, child, element)
    elif isinstance(value, list):
        for entry in value:
            _json_to_xml("item", entry, element)
    else:
        element.text = _text(value)


def _xml(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    action = str(_cfg(node, "action", ctx, "parse") or "parse").strip().lower()
    payloads = _item_payloads(ctx, inputs)

    if action == "parse":
        source = _cfg(node, "xml", ctx) or _first_text_field(payloads, ("xml", "data", "body"))
        if not source:
            return _fail("XML node needs an 'xml' value", "INVALID_INPUT")
        try:
            parsed = _xml_to_json(str(source))
        except ElementTree.ParseError as exc:
            return _fail(f"Invalid XML: {exc}", "INVALID_INPUT")
        return _outcome("succeeded", outputs={"data": parsed, "json": parsed})

    if action == "build":
        data = _cfg(node, "data", ctx)
        if data is None and payloads:
            data = payloads[0]
        root_name = str(_cfg(node, "root", ctx, "root") or "root")
        root = ElementTree.Element(root_name)
        if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), (dict, list)):
            inner_name, inner = next(iter(data.items()))
            _json_to_xml(str(inner_name), inner, root)
        elif isinstance(data, list):
            for entry in data:
                _json_to_xml("item", entry, root)
        elif isinstance(data, dict):
            for key, child in data.items():
                if str(key).startswith("@"):
                    root.set(str(key)[1:], _text(child))
                else:
                    _json_to_xml(str(key), child, root)
        else:
            root.text = _text(data)
        return _outcome(
            "succeeded",
            outputs={"xml": ElementTree.tostring(root, encoding="unicode")},
        )

    return _fail(f"Unknown xml action {action!r}")


# ---------------------------------------------------------------------------
# compression
# ---------------------------------------------------------------------------


def _binary_bytes(node: Dict[str, Any], ctx: Any) -> Optional[bytes]:
    from openagent.runtime.binary_store import read_binary
    from openagent.runtime.items import item_binary

    property_name = str(_cfg(node, "binary_property", ctx, "data") or "data")
    for item in _incoming(ctx, {}):
        props = item_binary(item)
        prop = props.get(property_name) or next(
            (value for value in props.values() if isinstance(value, dict)), None
        )
        if isinstance(prop, dict):
            try:
                return read_binary(prop)
            except Exception:  # noqa: BLE001 - a bad property falls back to text
                return None
    return None


def _compression(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    action = str(_cfg(node, "action", ctx, "gzip") or "gzip").strip().lower()
    raw = _cfg(node, "data", ctx)
    source: Optional[bytes] = None
    if isinstance(raw, str) and raw.strip():
        try:
            source = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            source = raw.encode("utf-8")
    elif raw is not None:
        source = _text(raw).encode("utf-8")
    if source is None:
        source = _binary_bytes(node, ctx)
    if source is None:
        return _fail("Compression node needs base64 data or a binary item", "INVALID_INPUT")

    size = len(source)
    try:
        if action == "gzip":
            output = gzip.compress(source)
        elif action == "gunzip":
            output = gzip.decompress(source)
        elif action == "deflate":
            output = zlib.compress(source)
        elif action == "inflate":
            output = zlib.decompress(source)
        elif action == "zip":
            name = str(_cfg(node, "file_name", ctx, "data.txt") or "data.txt")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(name, source)
            output = buffer.getvalue()
        elif action == "unzip":
            with zipfile.ZipFile(io.BytesIO(source)) as archive:
                names = archive.namelist()
                if not names:
                    return _fail("Zip archive is empty", "INVALID_INPUT")
                output = archive.read(names[0])
                return _outcome(
                    "succeeded",
                    outputs={
                        "data": base64.b64encode(output).decode("ascii"),
                        "file_name": names[0],
                        "files": names,
                        "input_bytes": size,
                        "output_bytes": len(output),
                    },
                )
        else:
            return _fail(f"Unknown compression action {action!r}")
    except (OSError, zlib.error, zipfile.BadZipFile) as exc:
        return _fail(f"Compression failed: {exc}", "INVALID_INPUT")

    payload = {
        "data": base64.b64encode(output).decode("ascii"),
        "action": action,
        "input_bytes": size,
        "output_bytes": len(output),
    }
    try:
        payload["text"] = output.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return _outcome("succeeded", outputs=payload)


# ---------------------------------------------------------------------------
# compare_datasets
# ---------------------------------------------------------------------------


def _row_key(row: Any, fields: List[str]) -> str:
    if not fields:
        return json.dumps(row, sort_keys=True, default=str)
    if len(fields) == 1:
        value = row.get(fields[0]) if isinstance(row, dict) else None
        return json.dumps(value, sort_keys=True, default=str)
    return json.dumps(
        [row.get(field) if isinstance(row, dict) else None for field in fields],
        default=str,
    )


def _compare_datasets(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    from openagent.runtime.items import as_items, item_json, make_item

    raw_a = _cfg(node, "items_a", ctx)
    raw_b = _cfg(node, "items_b", ctx)
    ordered = list(inputs.values())
    list_a = as_items(raw_a) if raw_a is not None else (as_items(ordered[0]) if ordered else [])
    list_b = (
        as_items(raw_b)
        if raw_b is not None
        else (as_items(ordered[1]) if len(ordered) > 1 else [])
    )
    if raw_a is not None and raw_b is None and len(ordered) > 1:
        list_b = as_items(ordered[1])

    fields = [
        part.strip()
        for part in str(_cfg(node, "field", ctx, "") or "").split(",")
        if part.strip()
    ]

    index_b: Dict[str, Any] = {}
    for item in list_b:
        index_b.setdefault(_row_key(item_json(item), fields), item_json(item))

    same: List[Any] = []
    different: List[Any] = []
    only_in_a: List[Any] = []
    seen: set[str] = set()
    for item in list_a:
        row = item_json(item)
        key = _row_key(row, fields)
        if key in index_b:
            seen.add(key)
            counterpart = index_b[key]
            if counterpart == row:
                same.append(row)
            else:
                changed = sorted(
                    field for field in {
                        *(row.keys() if isinstance(row, dict) else []),
                        *(counterpart.keys() if isinstance(counterpart, dict) else []),
                    }
                    if (row.get(field) if isinstance(row, dict) else None)
                    != (counterpart.get(field) if isinstance(counterpart, dict) else None)
                )
                different.append({"a": row, "b": counterpart, "fields": changed})
        else:
            only_in_a.append(row)
    only_in_b = [
        row for key, row in index_b.items() if key not in seen
    ]

    branches = {
        "same": same,
        "different": different,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
    }
    return _outcome(
        "succeeded",
        outputs={
            **branches,
            "counts": {name: len(rows) for name, rows in branches.items()},
        },
        items=[make_item(row) for row in [*same, *different, *only_in_a, *only_in_b]],
        ports=["same", "different", "only_in_a", "only_in_b"],
    )


# ---------------------------------------------------------------------------
# no_op / stop_and_error
# ---------------------------------------------------------------------------


def _no_op(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    from openagent.runtime.local_engine import _merged_input

    merged = _merged_input(inputs)
    return _outcome(
        "succeeded",
        outputs=merged if isinstance(merged, dict) else {"items": merged},
        items=_incoming(ctx, inputs) or None,
    )


def _stop_and_error(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    message = _text(_cfg(node, "message", ctx, "")).strip()
    return _outcome(
        "failed",
        error=message or "Stopped by the Stop and Error node",
        error_code="STOP_AND_ERROR",
    )


_HANDLERS = {
    "crypto": _crypto,
    "datetime": _datetime,
    "html": _html,
    "xml": _xml,
    "compression": _compression,
    "compare_datasets": _compare_datasets,
    "no_op": _no_op,
    "stop_and_error": _stop_and_error,
}


async def execute_core_extra(
    node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any
):
    """Run one Tier A core node; returns ``None`` for any other type."""
    handler = _HANDLERS.get(str(node.get("type") or ""))
    if handler is None:
        return None
    try:
        return handler(node, inputs, ctx)
    except Exception as exc:  # a bad config must not crash the run
        return _fail(f"{node.get('type')} node failed: {exc}", "NODE_EXCEPTION")
