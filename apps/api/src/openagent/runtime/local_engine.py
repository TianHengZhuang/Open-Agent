"""Local, in-process workflow execution engine.

Runs a workflow definition end-to-end without Redis, PostgreSQL or a worker
process — the "test workflow" path an n8n-style builder expects.

Deterministic nodes execute for real (triggers, set, transform, filter, map,
condition, switch, merge, loop, variable, delay, HTTP request). Nodes that
need an external provider or a human (AI prompt, agent, tool, connector,
coding, quality gates, approval) are recorded as ``skipped`` (or ``waiting``)
with an explicit reason instead of being faked. Every node run is persisted on
the execution row so the builder can show inputs, outputs, timing and errors.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.db.models import (
    ExecutionEvent,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowExecutionTriggerType,
    WorkflowVersion,
)
from openagent.runtime.crash_journal import journal_start
from openagent.runtime.expressions import (
    ExpressionData,
    evaluate_expression,
    evaluate_template,
)
from openagent.runtime.expressions import (
    ExpressionError as NativeExpressionError,
)
from openagent.runtime.items import as_items, is_item, item_json
from openagent.runtime.lifecycle import begin_run, end_run, is_shutting_down
from openagent.runtime.queue_mode import queue_mode_enabled
from openagent.runtime.workflow_ops import (
    active_executions,
    concurrency_limit,
    execution_timeout,
    save_execution_data_mode,
)
from openagent.services.model_runtime import (
    ModelRuntimeError,
    ModelTarget,
    chat_stream,
    with_model,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SYNC_DELAY_SECONDS = 10.0
HTTP_NODE_TIMEOUT_SECONDS = 20.0
MAX_LOOP_ITEMS = 1000
MAX_SUBWORKFLOW_DEPTH = 10
# Execution data is capped per node so one huge item list cannot bloat the
# execution row (n8n prunes/limits what the NDV persists the same way).
MAX_PERSISTED_ITEMS_PER_NODE = 100

_EXPR_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.S)
_PATH_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_COMPARISON_RE = re.compile(
    r"^(.*?)\s*(==|!=|>=|<=|>|<|not\s+contains|contains|starts\s+with|ends\s+with|not\s+in|in)\s*(.*)$",
    re.S,
)


class ExpressionError(Exception):
    """Raised when an expression cannot be resolved."""


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    trigger: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    item: Any = None
    index: int = 0
    # Every item that arrived at the node (``$input.all()``), when known.
    input_items: List[Any] = field(default_factory=list)
    # Sub-workflow plumbing (n8n "Execute Workflow"): the DB session used to
    # resolve the child definition, the tenant it must belong to, the current
    # nesting depth and the call stack used to reject cycles.
    db: Any = None
    organization_id: Optional[UUID] = None
    depth: int = 0
    stack: Tuple[str, ...] = ()
    # Persisted run identity and workflow static data. A sub-workflow call
    # creates a child execution row from these, and ``$getWorkflowStaticData``
    # reads/writes the shared dict that the run writes back when it finishes.
    execution_id: Optional[UUID] = None
    root_execution_id: Optional[UUID] = None
    static_data: Dict[str, Any] = field(default_factory=dict)
    # The organization's chat endpoint, when one was resolved. Without it a
    # ``prompt`` node is reported as skipped instead of being faked.
    model: Optional[ModelTarget] = None
    # Streaming sink for chat: deltas from a model node reach the open SSE
    # response as they arrive. ``None`` means "buffer the whole reply".
    token_sink: Optional[Callable[[str], Awaitable[None]]] = None

    def child(self, item: Any, index: int) -> "RunContext":
        return RunContext(
            trigger=self.trigger,
            variables=self.variables,
            outputs=self.outputs,
            item=item,
            index=index,
            input_items=self.input_items,
            db=self.db,
            organization_id=self.organization_id,
            depth=self.depth,
            stack=self.stack,
            execution_id=self.execution_id,
            root_execution_id=self.root_execution_id,
            static_data=self.static_data,
            model=self.model,
            token_sink=self.token_sink,
        )


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _persisted_items(
    items: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Cap the items stored on an execution row; returns (items, dropped)."""
    if not items:
        return [], 0
    limit = MAX_PERSISTED_ITEMS_PER_NODE
    if len(items) <= limit:
        return list(items), 0
    return list(items[:limit]), len(items) - limit


def parse_jsonish(value: Any, fallback: Any = None) -> Any:
    """Config values authored in JSON fields arrive as strings."""
    if value is None:
        return fallback
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return fallback


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "null", "none")
    if isinstance(value, (list, dict, set)):
        return len(value) > 0
    return True


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _NUMBER_RE.match(value.strip()):
        return float(value.strip())
    return None


# ---------------------------------------------------------------------------
# Expression resolution
# ---------------------------------------------------------------------------


def _path_parts(path: str) -> List[Any]:
    parts: List[Any] = []
    for match in _PATH_RE.finditer(path):
        name, index = match.groups()
        parts.append(name if name is not None else int(index))
    return parts


def _deep_get(value: Any, path: str) -> Any:
    current = value
    for part in _path_parts(path):
        if isinstance(part, int):
            if not isinstance(current, (list, tuple)):
                return None
            if part >= len(current):
                return None
            current = current[part]
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _lookup_root(name: str, ctx: RunContext) -> Any:
    lowered = name.lower()
    if lowered == "item":
        return ctx.item
    if lowered == "index":
        return ctx.index
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    if lowered == "now":
        return _now().isoformat()
    if name in ctx.variables:
        return ctx.variables[name]
    if name in ctx.trigger:
        return ctx.trigger[name]
    if name in ctx.outputs:
        return ctx.outputs[name]
    raise ExpressionError(f"Unknown reference: {name}")


def _resolve_body(body: str, ctx: RunContext) -> Any:
    text = body.strip()
    if not text:
        return None

    # String literal
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    if _NUMBER_RE.match(text):
        return float(text) if "." in text else int(text)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if text.lower() in ("null", "none"):
        return None

    head, _, rest = text.partition(".")
    namespace = head.strip()

    if namespace in ("trigger", "input"):
        base = ctx.trigger.get("input", ctx.trigger) if namespace == "input" else ctx.trigger
        return _deep_get(base, rest) if rest else base
    if namespace in ("nodes", "node"):
        if not rest:
            return ctx.outputs
        node_id, _, tail = rest.partition(".")
        if node_id not in ctx.outputs:
            raise ExpressionError(f"Node '{node_id}' has not run")
        return _deep_get(ctx.outputs[node_id], tail) if tail else ctx.outputs[node_id]
    if namespace in ("variables", "vars"):
        return _deep_get(ctx.variables, rest) if rest else ctx.variables
    if namespace == "env":
        key = rest.split(".")[0] if rest else ""
        return os.environ.get(key) if key else None
    if namespace == "run":
        return _deep_get({"id": ctx.trigger.get("_execution_id")}, rest) if rest else {}
    if namespace == "workflow":
        meta = ctx.trigger.get("_workflow") or {}
        return _deep_get(meta, rest) if rest else meta

    return _deep_get(_lookup_root(namespace, ctx), rest) if rest else _lookup_root(namespace, ctx)


def _current_item_json(ctx: RunContext) -> Any:
    """The JSON an expression sees as ``$json`` for this item.

    Node output is plain JSON in this engine; the chat trigger wraps its item
    n8n-style (``{json: {...}, history: [...]}``), so a wrapped current item or
    trigger is unwrapped to its payload.
    """
    item = ctx.item
    if item is not None:
        value = item["json"] if is_item(item) else item
    else:
        inner = ctx.trigger.get("json") if isinstance(ctx.trigger, dict) else None
        value = inner if isinstance(inner, dict) else (ctx.trigger if ctx.trigger else {})
    # A trigger payload may itself be an n8n wrapper (the chat trigger is
    # `{json: {...}, history: [...]}`); expressions read its payload.
    if isinstance(value, dict) and isinstance(value.get("json"), dict) and set(value) <= {
        "json",
        "binary",
        "pairedItem",
        "history",
        "input",
    }:
        return value["json"]
    return value


def expression_data(ctx: RunContext) -> ExpressionData:
    """Build the n8n data proxy for one evaluation."""
    current = _current_item_json(ctx)
    items = list(ctx.input_items) if ctx.input_items else (as_items(ctx.item) if ctx.item is not None else [])
    from openagent.runtime.items import first_binary

    return ExpressionData(
        json=current,
        legacy_json=current,
        items=items,
        binary=first_binary([item for item in items if isinstance(item, dict)]) if items else {},
        node_outputs=ctx.outputs,
        variables=ctx.variables,
        workflow=ctx.trigger.get("_workflow") or {},
        execution={"id": ctx.trigger.get("_execution_id")},
        static_data=ctx.static_data,
        trigger=ctx.trigger,
        item_index=ctx.index,
        run_index=0,
    )


def resolve(value: Any, ctx: RunContext) -> Any:
    """Resolve ``{{ ... }}`` expressions in any nested config value.

    n8n's expression grammar is tried first (``$json``, ``$node``, method
    calls, arithmetic, optional chaining); the original path-based resolver
    stays as the fallback so stored workflows keep evaluating unchanged.
    """
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

    try:
        return evaluate_template(value, expression_data(ctx))
    except NativeExpressionError:
        return _legacy_resolve(value, ctx)


def _legacy_resolve(value: Any, ctx: RunContext) -> Any:
    """Original path-based resolver, kept for workflows n8n's grammar rejects."""
    single = _EXPR_RE.fullmatch(value.strip())
    if single and "}}" not in single.group(1) and "{{" not in single.group(1):
        # Exactly one expression covering the whole string keeps its type.
        return _resolve_body(single.group(1), ctx)

    def replace(match: re.Match) -> str:
        try:
            return _stringify(_resolve_body(match.group(1), ctx))
        except ExpressionError:
            return ""

    return _EXPR_RE.sub(replace, value)


# ---------------------------------------------------------------------------
# Boolean expressions (condition / switch / filter)
# ---------------------------------------------------------------------------


def _strip_wrapper(expr: str) -> str:
    text = expr.strip()
    single = _EXPR_RE.fullmatch(text)
    return single.group(1).strip() if single else text


def _resolve_operand(raw: str, ctx: RunContext) -> Any:
    """Resolve one side of a comparison (literal or expression)."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    if _NUMBER_RE.match(text):
        return float(text) if "." in text else int(text)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if text.lower() in ("null", "none", ""):
        return None
    return resolve(text, ctx)


def _split_top(text: str, separator: str) -> List[str]:
    """Split on a keyword, ignoring quotes and nesting."""
    parts: List[str] = []
    depth = 0
    quote: Optional[str] = None
    start = 0
    i = 0
    sep_len = len(separator)
    lowered = text.lower()
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and lowered.startswith(separator, i):
            parts.append(text[start:i])
            i += sep_len
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def evaluate(expr: Any, ctx: RunContext) -> bool:
    if isinstance(expr, bool):
        return expr
    if expr is None:
        return False
    if not isinstance(expr, str):
        return _truthy(expr)

    text = _strip_wrapper(expr)
    if not text:
        return False

    # n8n's expression grammar first: {{ $json.total > 10 }}, {{ !$json.done }},
    # {{ $json.status === "ok" }}. Operator words below stay as the fallback.
    try:
        native = evaluate_expression(text, expression_data(ctx))
        if not isinstance(native, (dict, list)):
            return _truthy(native)
    except NativeExpressionError:
        pass

    or_parts = _split_top(text, " or ")
    if len(or_parts) > 1:
        return any(evaluate(part, ctx) for part in or_parts)
    and_parts = _split_top(text, " and ")
    if len(and_parts) > 1:
        return all(evaluate(part, ctx) for part in and_parts)
    if text.lower().startswith("not "):
        return not evaluate(text[4:], ctx)
    if text.startswith("(") and text.endswith(")"):
        return evaluate(text[1:-1], ctx)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"

    match = _COMPARISON_RE.match(text)
    if match:
        left_raw, operator, right_raw = match.groups()
        operator = re.sub(r"\s+", " ", operator.strip().lower())
        try:
            left = _resolve_operand(left_raw, ctx)
            right = _resolve_operand(right_raw, ctx)
        except ExpressionError:
            return False

        left_num, right_num = _as_number(left), _as_number(right)
        if operator == "==":
            if left_num is not None and right_num is not None:
                return left_num == right_num
            return left == right or _stringify(left) == _stringify(right)
        if operator == "!=":
            if left_num is not None and right_num is not None:
                return left_num != right_num
            return _stringify(left) != _stringify(right)
        if operator in (">", "<", ">=", "<="):
            if left_num is None or right_num is None:
                return False
            return {
                ">": left_num > right_num,
                "<": left_num < right_num,
                ">=": left_num >= right_num,
                "<=": left_num <= right_num,
            }[operator]
        if operator == "contains":
            if isinstance(left, (list, tuple)):
                return right in left
            return _stringify(right) in _stringify(left)
        if operator == "not contains":
            return _stringify(right) not in _stringify(left)
        if operator == "starts with":
            return _stringify(left).startswith(_stringify(right))
        if operator == "ends with":
            return _stringify(left).endswith(_stringify(right))
        if operator == "in":
            if isinstance(right, (list, tuple)):
                return left in right or _stringify(left) in [_stringify(r) for r in right]
            return _stringify(left) in _stringify(right)
        if operator == "not in":
            if isinstance(right, (list, tuple)):
                return left not in right
            return _stringify(left) not in _stringify(right)

    try:
        return _truthy(resolve(text, ctx))
    except ExpressionError:
        return False


# ---------------------------------------------------------------------------
# Node execution
# ---------------------------------------------------------------------------


@dataclass
class NodeOutcome:
    status: str  # succeeded | failed | skipped | waiting
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    ports: List[str] = field(default_factory=lambda: ["out"])
    reason: Optional[str] = None
    # Canonical n8n items for this node (`{json, pairedItem}`). Nodes that know
    # their item lineage set this; otherwise it is derived from ``outputs``.
    items: Optional[List[Dict[str, Any]]] = None
    # True when the failure was routed to the node's "error" output
    # (``on_error: continueErrorOutput``) and must not fail the workflow.
    handled: bool = False


def item_envelope(value: Any, source_index: Optional[int] = None) -> Dict[str, Any]:
    """Wrap one node output value as an n8n item."""
    from openagent.runtime.items import make_item

    payload = value
    if is_item(payload):
        payload = payload["json"]
    return make_item(payload, source_index)


def _items_from_pairs(pairs: List[Tuple[int, Any]]) -> List[Dict[str, Any]]:
    """Build items that remember which input item each one came from."""
    return [item_envelope(value, source) for source, value in pairs]


def _input_items(inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every item arriving at a node, in connection order."""
    collected: List[Dict[str, Any]] = []
    for value in inputs.values():
        collected.extend(as_items(value))
    return collected


def _cfg(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    config = node.get("config") or {}
    return config.get(key, default)


def _node_setting(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Node-level setting: the n8n-style top-level field wins over config."""
    value = node.get(key)
    if value is None:
        value = _cfg(node, key, default)
    return default if value is None else value


_ERROR_MODES = {
    "": "stop",
    "stop": "stop",
    "stopworkflow": "stop",
    "continue": "continue",
    "continueonerror": "continue",
    "continueerroroutput": "error_output",
    "continueonerroroutput": "error_output",
}


def _error_mode(node: Dict[str, Any]) -> str:
    """n8n's ``onError``: stop | continue | continueErrorOutput."""
    raw = _node_setting(node, "on_error", "")
    key = str(raw).lower().replace("_", "").replace("-", "").replace(" ", "")
    return _ERROR_MODES.get(key, "stop")


def _node_flag(node: Dict[str, Any], key: str) -> bool:
    value = _node_setting(node, key, False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _retry_plan(node: Dict[str, Any]) -> Tuple[int, Optional[float]]:
    """Attempts and per-retry wait (ms) from n8n or legacy retry fields."""
    retry_on_fail = _node_flag(node, "retry_on_fail")
    max_tries = _node_setting(node, "max_tries")
    policy = node.get("retry_policy") or {}
    if retry_on_fail:
        # n8n needs retryOnFail; maxTries alone is only a stored preference.
        try:
            tries = int(max_tries if max_tries is not None else 3)
        except (TypeError, ValueError):
            tries = 3
        attempts_allowed = max(1, min(tries, 11))
    else:
        try:
            extra_attempts = int(policy.get("max_attempts") or 0)
        except (TypeError, ValueError):
            extra_attempts = 0
        attempts_allowed = max(1, min(extra_attempts + 1, 11))

    raw_wait = _node_setting(node, "wait_between_tries_ms")
    wait_ms: Optional[float]
    if raw_wait is not None:
        try:
            wait_ms = max(0.0, min(float(raw_wait), 60000.0))
        except (TypeError, ValueError):
            wait_ms = None
    elif retry_on_fail:
        # n8n defaults to 1000 ms; local runs stay fast unless asked to wait.
        wait_ms = 0.0
    else:
        wait_ms = None
    return attempts_allowed, wait_ms


def _download_name(response: Any, url: str) -> str:
    """Filename for a downloaded binary response (Content-Disposition wins)."""
    disposition = str(response.headers.get("content-disposition") or "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^"\';]+)', disposition)
    if match:
        return match.group(1).strip()
    name = str(url).rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    return name or "download.bin"


def _carry_binary(outcome: "NodeOutcome", arrived: List[Dict[str, Any]]) -> None:
    """Keep a 1:1 node's binary payload downstream (a Set between file nodes)."""
    if outcome.items or len(arrived) != 1:
        return
    binary = arrived[0].get("binary") if isinstance(arrived[0], dict) else None
    if not isinstance(binary, dict) or not binary:
        return
    derived = as_items(outcome.outputs)
    if len(derived) != 1:
        return
    item = dict(derived[0])
    item["binary"] = binary
    outcome.items = [item]


def _ensure_output_data(outcome: "NodeOutcome") -> None:
    """n8n's ``alwaysOutputData``: an empty result still emits one item."""
    if outcome.items:
        return
    outputs = dict(outcome.outputs or {})
    existing = outputs.get("items")
    if isinstance(existing, list) and existing:
        return
    outcome.items = [item_envelope({})]
    outputs["items"] = [{}]
    outputs.setdefault("count", 0)
    outcome.outputs = outputs


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "data", "results", "body"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    if value is None:
        return []
    return [value]


def _field_names(value: Any) -> List[str]:
    """Comma-separated text or a list → the field names it names."""
    if isinstance(value, list):
        return [str(entry).strip() for entry in value if str(entry).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _field_value(item: Any, field: str) -> Any:
    """Read a dotted path out of an item (`customer.name`, `items.0.id`)."""
    if not field:
        return item
    current: Any = item
    for part in str(field).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _items_for(node: Dict[str, Any], inputs: Dict[str, Any], ctx: "RunContext") -> Any:
    """Resolve a node's items: explicit config wins, else the upstream output."""
    raw = _cfg(node, "items")
    value = resolve(raw, ctx) if raw else _merged_input(inputs)
    if isinstance(value, str):
        value = parse_jsonish(value, value)
    return value


async def _execute_with_policy(
    node: Dict[str, Any],
    inputs: Dict[str, Any],
    ctx: "RunContext",
    items: Optional[List[Dict[str, Any]]] = None,
) -> Tuple["NodeOutcome", int, bool]:
    """Run a node honouring retries, timeout and "continue on error" settings.

    Returns (outcome, attempts_used, continued). ``continued`` is True when the
    node failed but the node's ``config.on_error`` is "continue", in which case
    the failure is recorded and the input is passed downstream.
    """
    attempts_allowed, wait_ms = _retry_plan(node)

    raw_timeout = node.get("timeout_seconds")
    try:
        timeout = max(1.0, min(float(raw_timeout), 300.0)) if raw_timeout else None
    except (TypeError, ValueError):
        timeout = None

    error_mode = _error_mode(node)

    # n8n evaluates `$json` against the node's first incoming item, not the
    # trigger payload; node-level configs and conditions read it that way.
    working = ctx
    arrived = list(items) if items else _input_items(inputs)
    if arrived and _node_flag(node, "execute_once"):
        # n8n's "Execute Once": the node runs for the first item only.
        arrived = arrived[:1]
    if arrived:
        working.input_items = arrived
        if ctx.item is None:
            working = ctx.child(arrived[0], 0)
            working.input_items = arrived

    outcome = NodeOutcome(status="failed", error="Node was not executed", error_code="UNKNOWN")
    attempts_used = 0
    for attempt in range(1, attempts_allowed + 1):
        attempts_used = attempt
        try:
            if timeout is not None:
                outcome = await asyncio.wait_for(
                    _exec_node(node, inputs, working, items=arrived), timeout=timeout
                )
            else:
                outcome = await _exec_node(node, inputs, working, items=arrived)
        except asyncio.TimeoutError:
            outcome = NodeOutcome(
                status="failed",
                error=f"Timed out after {timeout:g}s",
                error_code="NODE_TIMEOUT",
            )
        except Exception as exc:  # a node bug must not kill the whole run
            outcome = NodeOutcome(status="failed", error=str(exc), error_code="NODE_EXCEPTION")

        if outcome.status != "failed":
            break
        if attempt < attempts_allowed:
            if wait_ms is not None:
                await asyncio.sleep(wait_ms / 1000.0)
            else:
                await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))

    if outcome.status == "failed" and error_mode == "error_output":
        # n8n's continueErrorOutput: the failure becomes one item on the
        # "error" output, a normal edge receives nothing, and the run itself
        # is not marked failed because the error was handled.
        payload = {
            "error": outcome.error or "Node failed",
            "error_code": outcome.error_code,
            "node": str(node.get("name") or node.get("id") or ""),
        }
        outcome.items = [item_envelope(payload)]
        outcome.outputs = {
            **(outcome.outputs or {}),
            "items": [payload],
            "count": 1,
            "error": outcome.error,
            "error_code": outcome.error_code,
        }
        outcome.ports = ["error"]
        outcome.handled = True
        return outcome, attempts_used, False

    if outcome.status == "failed" and error_mode == "continue":
        passthrough = _merged_input(inputs)
        outcome.outputs = passthrough if isinstance(passthrough, dict) else {"items": passthrough}
        return outcome, attempts_used, True

    if outcome.status == "succeeded" and not outcome.items:
        _carry_binary(outcome, arrived)
    if outcome.status == "succeeded" and _node_flag(node, "always_output_data"):
        _ensure_output_data(outcome)

    return outcome, attempts_used, False


def _merged_input(inputs: Dict[str, Any]) -> Any:
    values = list(inputs.values())
    if not values:
        return {}
    if len(values) == 1:
        return values[0]
    merged: Dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged if merged else values


async def _exec_trigger(trigger: Dict[str, Any], trigger_input: Dict[str, Any], ctx: RunContext) -> NodeOutcome:
    ttype = str(trigger.get("type") or "manual")
    config = trigger.get("config") or {}
    binary: Optional[Dict[str, Any]] = None
    if ttype == "webhook":
        incoming = dict(trigger_input)
        raw_binary = incoming.pop("__binary", None)
        if isinstance(raw_binary, dict) and raw_binary:
            binary = raw_binary
        payload = {
            "method": "POST",
            "headers": {},
            "body": incoming,
            "query": {},
            "path": config.get("path") or "",
            **incoming,
        }
    elif ttype == "schedule":
        payload = {"fired_at": _now().isoformat(), "cron": config.get("cron"), **trigger_input}
    elif ttype == "event":
        payload = {"event": config.get("event"), "payload": trigger_input, **trigger_input}
    elif ttype in ("execute_workflow", "execute_workflow_trigger", "subworkflow"):
        # n8n "Execute Workflow Trigger": the parent call's payload becomes
        # this flow's first item. ``config.inputs`` only documents the
        # expected shape, so declared keys appear with an empty default.
        inner = trigger_input.get("json")
        payload = dict(inner) if isinstance(inner, dict) else dict(trigger_input)
        declared = parse_jsonish(config.get("inputs"), {})
        if isinstance(declared, dict):
            for key in declared:
                payload.setdefault(str(key), None)
        payload["input"] = trigger_input
        ctx.trigger.update(payload)
        return NodeOutcome(status="succeeded", outputs=payload)
    elif ttype == "chat":
        # n8n "Chat Trigger" item: ``{json: {chatInput, sessionId, action, files}}``
        # plus the transcript the chat UI keeps locally, so a prompt node can
        # answer multi-turn without any server-side session store.
        item = trigger_input.get("json")
        payload = {
            "json": dict(item) if isinstance(item, dict) else dict(trigger_input),
            "history": list(trigger_input.get("history") or []),
        }
    else:
        payload = dict(trigger_input)

    payload["input"] = trigger_input
    ctx.trigger.update(payload)
    outcome = NodeOutcome(status="succeeded", outputs=payload)
    if binary is not None:
        # Webhook upload: the file rides on the trigger item, as in n8n where
        # the webhook node exposes `binary` next to the parsed JSON.
        outcome.items = [{**item_envelope(payload), "binary": binary}]
    return outcome


async def _exec_subworkflow(node: Dict[str, Any], inputs: Dict[str, Any], ctx: RunContext) -> NodeOutcome:
    """Run another workflow in-process (n8n "Execute Workflow").

    Only workflows in the same organization are reachable. Nesting is bounded
    by ``MAX_SUBWORKFLOW_DEPTH`` and the call stack rejects cycles, so a
    workflow cannot recurse into itself.
    """
    if ctx.db is None:
        return NodeOutcome(
            status="failed",
            error="Subworkflow node requires a database session",
            error_code="NO_CONTEXT",
        )

    raw_target = str(resolve(_cfg(node, "workflow_id") or "", ctx) or "").strip()
    if not raw_target:
        return NodeOutcome(
            status="failed",
            error="Subworkflow node needs config.workflow_id",
            error_code="INVALID_CONFIG",
        )
    try:
        target_id = UUID(raw_target)
    except (TypeError, ValueError):
        return NodeOutcome(
            status="failed",
            error=f"Subworkflow id {raw_target!r} is not a valid workflow id",
            error_code="INVALID_CONFIG",
        )

    if ctx.depth >= MAX_SUBWORKFLOW_DEPTH:
        return NodeOutcome(
            status="failed",
            error=f"Subworkflow nesting exceeded {MAX_SUBWORKFLOW_DEPTH} levels",
            error_code="SUBWORKFLOW_DEPTH_EXCEEDED",
        )
    if str(target_id) in ctx.stack:
        return NodeOutcome(
            status="failed",
            error=f"Subworkflow cycle detected at {target_id}",
            error_code="SUBWORKFLOW_CYCLE",
        )

    query = select(Workflow).where(
        Workflow.id == target_id,
        Workflow.deleted_at.is_(None),
    )
    if ctx.organization_id is not None:
        query = query.where(Workflow.organization_id == ctx.organization_id)
    target = (await ctx.db.execute(query)).scalar_one_or_none()
    if target is None:
        return NodeOutcome(
            status="failed",
            error=f"Workflow {raw_target} was not found in this organization",
            error_code="SUBWORKFLOW_NOT_FOUND",
        )

    version = (
        await ctx.db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == target.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version is None:
        return NodeOutcome(
            status="failed",
            error=f"Workflow '{target.name}' has no saved version to run",
            error_code="SUBWORKFLOW_NO_VERSION",
        )

    raw_mode = str(resolve(_cfg(node, "mode"), ctx) or "wait").strip().lower()
    if raw_mode in ("run", "async", "fire-and-forget"):
        # n8n can start a sub-workflow without waiting. The local engine is
        # in-process and has no queue, so this fails loudly instead of
        # pretending the child ran.
        return NodeOutcome(
            status="failed",
            error="mode 'run' (fire-and-forget) needs queue mode; use mode 'wait'",
            error_code="SUBWORKFLOW_ASYNC_UNSUPPORTED",
        )

    payload = resolve(_cfg(node, "input"), ctx)
    if payload is None:
        payload = _merged_input(inputs)
    if not isinstance(payload, dict):
        payload = {"input": payload}

    # Execution tree: a persisted parent gets a real child execution row, so
    # the run detail can show parent -> child and cancellation can cascade.
    # A definition-only run (engine tests) has no row to attach to.
    child_execution: Optional[WorkflowExecution] = None
    child_static: Dict[str, Any] = {}
    stored_static = getattr(target, "static_data", None)
    if isinstance(stored_static, dict):
        child_static.update(json.loads(json.dumps(stored_static, default=str)))
    if ctx.db is not None and ctx.execution_id is not None:
        child_execution = WorkflowExecution(
            organization_id=ctx.organization_id,
            workflow_id=target.id,
            workflow_version_id=version.id,
            status=WorkflowExecutionStatus.RUNNING,
            trigger_type=WorkflowExecutionTriggerType.API,
            started_at=_now(),
            parent_execution_id=ctx.execution_id,
            root_execution_id=ctx.root_execution_id or ctx.execution_id,
            metadata={
                "run_source": "subworkflow",
                "parent_node": str(node.get("id") or ""),
                "parent_node_name": str(node.get("name") or ""),
                "depth": ctx.depth + 1,
                "trigger_input": payload,
            },
        )
        ctx.db.add(child_execution)
        await ctx.db.flush()

    parent = RunContext(
        db=ctx.db,
        organization_id=ctx.organization_id,
        depth=ctx.depth + 1,
        stack=ctx.stack + (str(target_id),),
        execution_id=child_execution.id if child_execution is not None else ctx.execution_id,
        root_execution_id=(
            (ctx.root_execution_id or ctx.execution_id)
            if child_execution is not None
            else ctx.root_execution_id
        ),
        static_data=child_static,
        model=ctx.model,
        token_sink=ctx.token_sink,
    )
    runs, output, status = await execute_definition(
        version.definition or {}, payload, parent=parent
    )

    failed = [run for run in runs if run["status"] == "failed" and not run.get("continued")]
    outputs = {
        "workflow_id": str(target.id),
        "workflow_name": target.name,
        "version": version.version,
        "status": status,
        "execution_id": str(child_execution.id) if child_execution is not None else None,
        "root_execution_id": (
            str(ctx.root_execution_id or ctx.execution_id)
            if child_execution is not None
            else None
        ),
        "output": output.get("terminal") or output,
        "steps": len(runs),
        "node_runs": [
            {
                "node_id": run["node_id"],
                "node_name": run["node_name"],
                "status": run["status"],
                "error": run["error"],
            }
            for run in runs
        ],
    }

    if child_execution is not None:
        completed_at = _now()
        status_map = {
            "completed": WorkflowExecutionStatus.COMPLETED,
            "failed": WorkflowExecutionStatus.FAILED,
            "waiting": WorkflowExecutionStatus.WAITING,
        }
        child_execution.status = status_map.get(status, WorkflowExecutionStatus.FAILED)
        child_execution.completed_at = completed_at
        child_execution.error_code = "NODE_FAILED" if failed else None
        child_execution.error_message = (
            "; ".join(
                f"{run['node_name']}: {run['error']}" for run in failed if run.get("error")
            )[:2000]
            or None
        )
        child_execution.metadata = {
            **(child_execution.metadata or {}),
            "node_runs": runs,
            "output": output,
            "summary": {
                "total": len(runs),
                "succeeded": sum(1 for run in runs if run["status"] == "succeeded"),
                "failed": len(failed),
                "skipped": sum(1 for run in runs if run["status"] == "skipped"),
                "waiting": sum(1 for run in runs if run["status"] == "waiting"),
                "duration_ms": int((completed_at - (child_execution.started_at or completed_at)).total_seconds() * 1000),
            },
        }
        # Static data written inside the child belongs to the child workflow.
        target.static_data = json.loads(json.dumps(child_static, default=str))

    if status == "waiting":
        # A synchronous call cannot park; the parent node reports it instead of
        # claiming the child finished.
        return NodeOutcome(
            status="failed",
            outputs=outputs,
            error="Sub-workflow is waiting (approval or timed wait); a waiting child cannot complete a synchronous call",
            error_code="SUBWORKFLOW_WAITING",
        )
    if failed:
        return NodeOutcome(
            status="failed",
            outputs=outputs,
            error="; ".join(f"{r['node_name']}: {r['error']}" for r in failed if r.get("error"))[:1000]
            or f"Subworkflow '{target.name}' failed",
            error_code="SUBWORKFLOW_FAILED",
        )
    return NodeOutcome(status="succeeded", outputs=outputs)


async def _exec_prompt_node(
    node: Dict[str, Any], inputs: Dict[str, Any], ctx: "RunContext"
) -> NodeOutcome:
    """A single model call (n8n "AI Agent / LLM chain" minus tools).

    The prompt is a normal expression, so ``{{json.chatInput}}`` and
    ``{{nodes.classify.text}}`` both work. Chat history arrives on the chat
    trigger, which is how a locally stored transcript reaches the model.
    """
    if ctx.model is None:
        return NodeOutcome(
            status="skipped",
            outputs={
                "skipped": True,
                "reason": "SKIPPED_REQUIRES_PROMPT",
                "requires": "a model provider",
                "input": _merged_input(inputs),
            },
            reason="SKIPPED_REQUIRES_PROMPT",
        )

    system = str(resolve(_cfg(node, "system"), ctx) or "").strip()
    raw_prompt = _cfg(node, "prompt")
    prompt = str(resolve(raw_prompt, ctx) or "").strip() if raw_prompt else ""
    if not prompt:
        chat = ctx.trigger.get("json")
        if isinstance(chat, dict):
            prompt = str(chat.get("chatInput") or "").strip()
    if not prompt:
        return NodeOutcome(
            status="failed",
            error="Prompt node needs a prompt (or a chat input upstream)",
            error_code="INVALID_CONFIG",
        )

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for message in ctx.trigger.get("history") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})

    model_name = str(resolve(_cfg(node, "model"), ctx) or "").strip()
    target = with_model(ctx.model, model_name) if model_name else ctx.model
    max_tokens_raw = _as_number(_cfg(node, "max_tokens"))
    max_tokens = int(max_tokens_raw) if max_tokens_raw and max_tokens_raw > 0 else None
    temperature_raw = _as_number(_cfg(node, "temperature"))
    temperature = 0.2 if temperature_raw is None else max(0.0, min(temperature_raw, 2.0))

    try:
        reply = await chat_stream(
            target,
            messages,
            on_delta=ctx.token_sink,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except ModelRuntimeError as exc:
        return NodeOutcome(
            status="failed",
            outputs={"model": target.model, "messages": messages},
            error=str(exc.message)[:1000],
            error_code="MODEL_ERROR",
        )

    return NodeOutcome(
        status="succeeded",
        outputs={
            "text": reply,
            "model": target.model,
            "provider": target.provider,
            "messages": len(messages),
        },
    )


def _run_mode(node: Dict[str, Any]) -> str:
    """n8n-style run mode: one pass for all items, or one per item."""
    raw = node.get("run_mode")
    if raw is None:
        raw = _cfg(node, "run_mode")
    mode = str(raw or "all").strip().lower()
    return "each" if mode in ("each", "per_item", "per-item") else "all"


async def _exec_node(
    node: Dict[str, Any],
    inputs: Dict[str, Any],
    ctx: RunContext,
    items: Optional[List[Dict[str, Any]]] = None,
) -> NodeOutcome:
    """Run a node, honouring its run mode.

    ``run_mode: "all"`` (the default, and what every stored workflow uses)
    runs the body once with the merged input. ``run_mode: "each"`` runs the
    body per input item, with ``$json`` bound to that item, and collects the
    per-item results into an item list — n8n's "Run Once for Each Item".
    """
    if _run_mode(node) != "each":
        return await _exec_node_once(node, inputs, ctx)

    items = list(items) if items else _input_items(inputs)
    if not items:
        return await _exec_node_once(node, inputs, ctx)

    collected: List[Tuple[int, Any]] = []
    for index, item in enumerate(items[:MAX_LOOP_ITEMS]):
        child = ctx.child(item, index)
        child.input_items = items
        outcome = await _exec_node_once(node, {"item": item}, child)
        if outcome.status == "waiting":
            return outcome
        if outcome.status == "failed":
            return NodeOutcome(
                status="failed",
                outputs={"items": [value for _, value in collected], "count": len(collected)},
                error=outcome.error,
                error_code=outcome.error_code,
                items=_items_from_pairs(collected),
            )
        payload = outcome.outputs
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            payload = payload["items"]
            if len(payload) == 1:
                payload = payload[0]
        collected.append((index, payload))

    return NodeOutcome(
        status="succeeded",
        outputs={"items": [value for _, value in collected], "count": len(collected)},
        items=_items_from_pairs(collected),
    )


async def _exec_node_once(node: Dict[str, Any], inputs: Dict[str, Any], ctx: RunContext) -> NodeOutcome:
    node_type = str(node.get("type") or "")
    merged = _merged_input(inputs)

    if node_type in ("convert_to_file", "extract_from_file", "read_binary_file", "write_binary_file"):
        from openagent.runtime.executors.nodes.files import execute_file_node

        return await execute_file_node(node, inputs, ctx)

    if node_type in (
        "crypto",
        "datetime",
        "html",
        "xml",
        "compression",
        "compare_datasets",
        "no_op",
        "stop_and_error",
    ):
        from openagent.runtime.executors.nodes.core_extras import execute_core_extra

        return await execute_core_extra(node, inputs, ctx)

    if node_type == "code":
        from openagent.runtime.executors.nodes.sandbox_code import (
            execute_code_node,
            is_sandboxed_code,
        )

        # `language: python|javascript` runs in the sandboxed runner; without it
        # the original expression behaviour below is unchanged.
        if is_sandboxed_code(node):
            return await execute_code_node(node, inputs, ctx)

    if node_type in ("prompt", "llm", "chat_model"):
        return await _exec_prompt_node(node, inputs, ctx)

    if node_type == "set":
        values = parse_jsonish(_cfg(node, "values"), {})
        if not isinstance(values, dict):
            return NodeOutcome(status="failed", error="Set node needs a values object", error_code="INVALID_CONFIG")
        return NodeOutcome(status="succeeded", outputs=resolve(values, ctx))

    if node_type == "transform":
        mapping = _cfg(node, "mapping") or _cfg(node, "expression")
        if not mapping:
            return NodeOutcome(status="failed", error="Transform node needs a mapping", error_code="INVALID_CONFIG")
        parsed = parse_jsonish(mapping, None)
        result = resolve(parsed if parsed is not None else mapping, ctx)
        if not isinstance(result, dict):
            result = {"result": result}
        return NodeOutcome(status="succeeded", outputs=result)

    if node_type in ("filter", "map"):
        items = resolve(_cfg(node, "items"), ctx)
        if isinstance(items, str):
            items = parse_jsonish(items, items)
        if not isinstance(items, list):
            return NodeOutcome(
                status="failed",
                error=f"{node_type.title()} node expects a list, got {type(items).__name__}",
                error_code="INVALID_INPUT",
            )
        expression = _cfg(node, "predicate") if node_type == "filter" else _cfg(node, "expression")
        if not expression:
            return NodeOutcome(status="failed", error=f"{node_type.title()} node needs an expression", error_code="INVALID_CONFIG")
        pairs: List[Tuple[int, Any]] = []
        for index, item in enumerate(items[:MAX_LOOP_ITEMS]):
            item_ctx = ctx.child(item, index)
            if node_type == "filter":
                if evaluate(expression, item_ctx):
                    pairs.append((index, item))
            else:
                pairs.append((index, resolve(expression, item_ctx)))
        return NodeOutcome(
            status="succeeded",
            outputs={"items": [value for _, value in pairs], "count": len(pairs)},
            items=_items_from_pairs(pairs),
        )

    if node_type in ("sort", "limit", "remove_duplicates", "split_out"):
        source_items = as_items(_items_for(node, inputs, ctx))
        items = [item_json(entry) for entry in source_items]

        if node_type == "sort":
            field = str(_cfg(node, "field") or "").strip()
            descending = str(_cfg(node, "direction") or "asc").strip().lower() == "desc"
            numeric = _truthy(_cfg(node, "numeric"))

            def sort_key(item: Any) -> Any:
                value = _field_value(item, field)
                if numeric:
                    try:
                        return float(value)  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        return float("-inf")
                if isinstance(value, (dict, list)):
                    return json.dumps(value, sort_keys=True, default=str)
                return "" if value is None else str(value)

            indexed = list(enumerate(items))[:MAX_LOOP_ITEMS]
            ordered = sorted(indexed, key=lambda pair: sort_key(pair[1]), reverse=descending)
            pairs = [(index, value) for index, value in ordered]
            return NodeOutcome(
                status="succeeded",
                outputs={"items": [value for _, value in pairs], "count": len(pairs)},
                items=_items_from_pairs(pairs),
            )

        if node_type == "limit":
            try:
                maximum = int(_as_number(_cfg(node, "max_items")) or 10)
            except (TypeError, ValueError):
                maximum = 10
            maximum = max(0, min(maximum, MAX_LOOP_ITEMS))
            keep = str(_cfg(node, "keep") or "first").strip().lower()
            indexed = list(enumerate(items))
            picked = indexed[-maximum:] if keep == "last" and maximum else indexed[:maximum]
            pairs = [(index, value) for index, value in picked]
            return NodeOutcome(
                status="succeeded",
                outputs={"items": [value for _, value in pairs], "count": len(pairs)},
                items=_items_from_pairs(pairs),
            )

        if node_type == "remove_duplicates":
            key = str(_cfg(node, "key") or "").strip()
            seen: set[str] = set()
            pairs: List[Tuple[int, Any]] = []
            for index, item in enumerate(items[:MAX_LOOP_ITEMS]):
                value = _field_value(item, key) if key else item
                marker = json.dumps(value, sort_keys=True, default=str)
                if marker in seen:
                    continue
                seen.add(marker)
                pairs.append((index, item))
            return NodeOutcome(
                status="succeeded",
                outputs={
                    "items": [value for _, value in pairs],
                    "count": len(pairs),
                    "duplicates_removed": len(items) - len(pairs),
                },
                items=_items_from_pairs(pairs),
            )

        # split_out
        field = str(_cfg(node, "field") or "").strip()
        if not field:
            return NodeOutcome(
                status="failed",
                error="Split out node needs a field to split on",
                error_code="INVALID_CONFIG",
            )
        pairs: List[Tuple[int, Any]] = []
        for index, item in enumerate(items[:MAX_LOOP_ITEMS]):
            value = _field_value(item, field)
            if isinstance(value, list):
                pairs.extend((index, entry) for entry in value)
            elif value is None:
                pairs.append((index, item))
            else:
                pairs.append((index, value))
        return NodeOutcome(
            status="succeeded",
            outputs={"items": [value for _, value in pairs], "count": len(pairs)},
            items=_items_from_pairs(pairs),
        )

    if node_type == "edit_fields":
        # n8n "Edit Fields / Set": add, keep or remove fields per item.
        raw_fields = parse_jsonish(_cfg(node, "fields"), {})
        if raw_fields is not None and not isinstance(raw_fields, dict):
            return NodeOutcome(
                status="failed",
                error="Edit fields node needs a fields object",
                error_code="INVALID_CONFIG",
            )
        fields = raw_fields or {}
        keep = _field_names(_cfg(node, "keep_only"))
        remove = _field_names(_cfg(node, "remove"))
        source = as_items(_items_for(node, inputs, ctx))
        pairs: List[Tuple[int, Any]] = []
        for index, entry in enumerate(source[:MAX_LOOP_ITEMS]):
            data = item_json(entry)
            if keep:
                result = {key: data[key] for key in keep if isinstance(data, dict) and key in data}
            else:
                result = dict(data) if isinstance(data, dict) else {"value": data}
            if fields:
                child = ctx.child(entry, index)
                child.input_items = source
                resolved = resolve(fields, child)
                if isinstance(resolved, dict):
                    result.update(resolved)
            for key in remove:
                result.pop(key, None)
            pairs.append((index, result))
        return NodeOutcome(
            status="succeeded",
            outputs={"items": [value for _, value in pairs], "count": len(pairs)},
            items=_items_from_pairs(pairs),
        )

    if node_type == "aggregate":
        mode = str(_cfg(node, "mode") or "collect").strip().lower()
        if mode in ("group", "groupby", "group-by"):
            mode = "group_by"
        field = str(_cfg(node, "field") or "").strip()
        group_field = str(_cfg(node, "group_by") or "").strip()
        source = as_items(_items_for(node, inputs, ctx))
        values = [item_json(entry) for entry in source[:MAX_LOOP_ITEMS]]

        if mode == "count":
            return NodeOutcome(
                status="succeeded",
                outputs={"count": len(values), "items": [{"count": len(values)}]},
            )
        if mode == "group_by":
            if not group_field:
                return NodeOutcome(
                    status="failed",
                    error="Aggregate group_by needs config.group_by",
                    error_code="INVALID_CONFIG",
                )
            groups: Dict[str, Dict[str, Any]] = {}
            for index, value in enumerate(values):
                key = _stringify(_field_value(value, group_field))
                bucket = groups.setdefault(
                    key,
                    {"key": _field_value(value, group_field), "count": 0, "items": [], "first": index},
                )
                bucket["count"] += 1
                bucket["items"].append(value)
            pairs = [
                (bucket.pop("first"), {k: v for k, v in bucket.items()})
                for bucket in groups.values()
            ]
            return NodeOutcome(
                status="succeeded",
                outputs={"groups": [value for _, value in pairs], "count": len(pairs)},
                items=_items_from_pairs(pairs),
            )
        if mode not in ("collect", "sum", "average"):
            return NodeOutcome(
                status="failed",
                error=f"Unknown aggregate mode '{mode}'",
                error_code="INVALID_CONFIG",
            )
        if not field:
            return NodeOutcome(
                status="failed",
                error=f"Aggregate {mode} needs config.field",
                error_code="INVALID_CONFIG",
            )
        collected = [_field_value(value, field) for value in values]
        if mode == "collect":
            payload: Dict[str, Any] = {field: collected, "count": len(collected)}
        else:
            numbers = [_as_number(value) for value in collected]
            usable = [number for number in numbers if number is not None]
            total = sum(usable)
            if mode == "sum":
                payload = {"sum": total, "count": len(usable), "field": field}
            else:
                payload = {
                    "average": (total / len(usable)) if usable else None,
                    "count": len(usable),
                    "field": field,
                }
        return NodeOutcome(status="succeeded", outputs=payload)

    if node_type == "split_in_batches":
        raw_size = _as_number(_cfg(node, "batch_size")) if _cfg(node, "batch_size") is not None else 10
        batch_size = int(raw_size) if raw_size and raw_size >= 1 else 10
        batch_size = min(batch_size, 1000)
        source = as_items(_items_for(node, inputs, ctx))[:MAX_LOOP_ITEMS]
        pairs: List[Tuple[int, Any]] = []
        for start in range(0, len(source), batch_size):
            chunk = source[start : start + batch_size]
            pairs.append(
                (
                    start,
                    {
                        "batch": [item_json(entry) for entry in chunk],
                        "index": len(pairs),
                        "size": len(chunk),
                        "total": len(source),
                    },
                )
            )
        return NodeOutcome(
            status="succeeded",
            outputs={"items": [value for _, value in pairs], "count": len(pairs)},
            items=_items_from_pairs(pairs),
        )

    if node_type == "code":
        raw_code = _cfg(node, "code")
        if not raw_code or not str(raw_code).strip():
            return NodeOutcome(
                status="failed",
                error="Code node needs config.code (an expression)",
                error_code="INVALID_CONFIG",
            )
        result = resolve(raw_code, ctx)
        if isinstance(result, str):
            result = parse_jsonish(result, result)
        if isinstance(result, list):
            pairs = [(index, value) for index, value in enumerate(result[:MAX_LOOP_ITEMS])]
            return NodeOutcome(
                status="succeeded",
                outputs={"items": [value for _, value in pairs], "count": len(pairs)},
                items=_items_from_pairs(pairs),
            )
        outputs = result if isinstance(result, dict) else {"result": result}
        return NodeOutcome(status="succeeded", outputs=outputs)

    if node_type == "respond_to_webhook":
        raw_status = _as_number(_cfg(node, "status")) if _cfg(node, "status") is not None else 200
        code = int(raw_status) if raw_status and 100 <= raw_status <= 599 else 200
        body = parse_jsonish(_cfg(node, "body"), None)
        if body is None:
            body = resolve(_cfg(node, "body"), ctx)
        elif isinstance(body, (dict, list)):
            body = resolve(body, ctx)
        headers = parse_jsonish(_cfg(node, "headers"), {}) or {}
        return NodeOutcome(
            status="succeeded",
            outputs={
                "responded": True,
                "status": code,
                "body": body,
                "headers": {str(k): str(v) for k, v in headers.items()}
                if isinstance(headers, dict)
                else {},
            },
        )

    if node_type == "condition":
        result = evaluate(_cfg(node, "expression"), ctx)
        return NodeOutcome(
            status="succeeded",
            outputs={"result": result, "branch": "true" if result else "false"},
            ports=["true" if result else "false"],
        )

    if node_type == "switch":
        routes = parse_jsonish(_cfg(node, "routes"), []) or []
        for route in routes:
            if not isinstance(route, dict):
                continue
            name = str(route.get("name") or "").strip()
            if name and evaluate(route.get("expression"), ctx):
                return NodeOutcome(status="succeeded", outputs={"route": name}, ports=[name])
        return NodeOutcome(status="succeeded", outputs={"route": "default"}, ports=["default"])

    if node_type == "merge":
        # n8n Merge: fan-in of item lists (append), positional combine, or a
        # key-matched combine. The legacy `merged` object is kept for callers
        # that read the old single-object output.
        mode = str(_cfg(node, "mode") or "append").strip().lower()
        if mode in ("combine", "by_index", "merge_by_index", "merge-by-index", "by-index", "index"):
            mode = "by_index"
        elif mode in ("by_key", "merge_by_key", "merge-by-key", "by-key", "key", "combine_by_key"):
            mode = "by_key"
        elif mode in ("choose", "choose_branch", "choose-branch", "first"):
            mode = "first"
        else:
            mode = "append"

        ports: List[Tuple[str, List[Dict[str, Any]]]] = [
            (str(label), as_items(value)) for label, value in inputs.items()
        ]
        if not ports:
            return NodeOutcome(
                status="succeeded",
                outputs={"items": [], "count": 0, "mode": mode, "merged": {}},
                items=[],
            )

        pairs: List[Tuple[int, Any]] = []
        if mode == "first":
            for _, source_items in ports:
                if source_items:
                    pairs = [
                        (index, item_json(entry))
                        for index, entry in enumerate(source_items[:MAX_LOOP_ITEMS])
                    ]
                    break
        elif mode == "by_index":
            longest = max((len(source) for _, source in ports), default=0)
            for index in range(min(longest, MAX_LOOP_ITEMS)):
                combined: Dict[str, Any] = {}
                for _, source_items in ports:
                    if index >= len(source_items):
                        continue
                    value = item_json(source_items[index])
                    if isinstance(value, dict):
                        combined.update(value)
                    else:
                        combined[f"value_{index}"] = value
                pairs.append((index, combined))
        elif mode == "by_key":
            raw_field = _cfg(node, "field") or _cfg(node, "fields") or ""
            if isinstance(raw_field, list):
                raw_field = raw_field[0] if raw_field else ""
            field = str(raw_field).strip()
            if not field:
                return NodeOutcome(
                    status="failed",
                    error="Merge by key needs config.field (the matching field)",
                    error_code="INVALID_CONFIG",
                )
            base_items = ports[0][1]
            others = [source for _, source in ports[1:]]
            for index, entry in enumerate(base_items[:MAX_LOOP_ITEMS]):
                value = item_json(entry)
                key = _stringify(_field_value(value, field))
                combined = dict(value) if isinstance(value, dict) else {"value": value}
                for other in others:
                    match = next(
                        (
                            item_json(candidate)
                            for candidate in other
                            if _stringify(_field_value(item_json(candidate), field)) == key
                        ),
                        None,
                    )
                    if isinstance(match, dict):
                        combined.update(match)
                pairs.append((index, combined))
        else:  # append
            for _, source_items in ports:
                for entry in source_items[:MAX_LOOP_ITEMS]:
                    # pairedItem stays relative to the node's concatenated input.
                    pairs.append((len(pairs), item_json(entry)))

        merged_items = _items_from_pairs(pairs)
        merged_output: Dict[str, Any] = {}
        for value in inputs.values():
            if isinstance(value, dict):
                merged_output.update(value)
        return NodeOutcome(
            status="succeeded",
            outputs={
                "items": [entry["json"] for entry in merged_items],
                "count": len(merged_items),
                "mode": mode,
                "merged": merged_output or merged,
            },
            items=merged_items,
        )

    if node_type == "loop":
        items = resolve(_cfg(node, "items"), ctx)
        if isinstance(items, str):
            items = parse_jsonish(items, items)
        if not isinstance(items, list):
            return NodeOutcome(status="failed", error="Loop node expects a list of items", error_code="INVALID_INPUT")
        limit = int(_as_number(_cfg(node, "max_iterations")) or 100)
        bounded = items[: max(1, min(limit, MAX_LOOP_ITEMS))]
        return NodeOutcome(
            status="succeeded",
            outputs={"items": bounded, "count": len(bounded), "mode": "single-pass"},
        )

    if node_type == "variable":
        mode = str(_cfg(node, "mode") or "set")
        name = str(resolve(_cfg(node, "name") or "", ctx) or "").strip()
        if not name:
            return NodeOutcome(status="failed", error="Variable node needs a name", error_code="INVALID_CONFIG")
        if mode == "get":
            return NodeOutcome(status="succeeded", outputs={"name": name, "value": ctx.variables.get(name)})
        value = resolve(_cfg(node, "value"), ctx)
        ctx.variables[name] = value
        return NodeOutcome(status="succeeded", outputs={"name": name, "value": value})

    if node_type == "delay":
        seconds = _as_number(resolve(_cfg(node, "duration_seconds", 0), ctx)) or 0
        wait = max(0.0, min(float(seconds), MAX_SYNC_DELAY_SECONDS))
        wants_webhook = _truthy(_cfg(node, "wait_for_webhook")) or str(
            _cfg(node, "mode") or ""
        ).strip().lower() in ("webhook", "on_webhook", "on-webhook")
        if wants_webhook and ctx.db is not None and ctx.depth == 0:
            # n8n's Wait node "On Webhook Call": park until an external caller
            # resumes the execution (POST .../webhook-resume), not until a time.
            return NodeOutcome(
                status="waiting",
                outputs={
                    "waiting": True,
                    "resume": "webhook",
                    "requested_seconds": float(seconds),
                    "resume_at": None,
                },
                reason="WAITING_FOR_WEBHOOK",
            )
        if float(seconds) > wait and ctx.db is not None and ctx.depth == 0:
            # n8n's Wait node: the run parks and a scheduler resumes it, instead
            # of capping the delay at the synchronous limit.
            resume_at = _now() + timedelta(seconds=float(seconds))
            return NodeOutcome(
                status="waiting",
                outputs={
                    "waiting": True,
                    "waited_seconds": 0.0,
                    "requested_seconds": float(seconds),
                    "resume_at": resume_at.isoformat(),
                },
                reason="WAITING_UNTIL",
            )
        if wait:
            await asyncio.sleep(wait)
        return NodeOutcome(status="succeeded", outputs={"waited_seconds": wait, "capped": float(seconds) > wait})

    if node_type == "webhook":
        url = str(resolve(_cfg(node, "url") or "", ctx) or "").strip()
        method = str(_cfg(node, "method") or "POST").upper()
        if not url.lower().startswith(("http://", "https://")):
            return NodeOutcome(status="failed", error="HTTP node needs an http(s) URL", error_code="INVALID_CONFIG")
        headers = parse_jsonish(_cfg(node, "headers"), {}) or {}
        body = parse_jsonish(resolve(_cfg(node, "body"), ctx), None)
        try:
            async with httpx.AsyncClient(timeout=HTTP_NODE_TIMEOUT_SECONDS, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    url,
                    headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
                    json=body if body is not None and method not in ("GET", "HEAD") else None,
                )
            parsed: Any
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            is_binary = bool(response.content) and not (
                "json" in content_type
                or content_type.startswith("text/")
                or content_type in ("application/xml", "application/xhtml+xml")
                or content_type.endswith("+xml")
            )
            if is_binary:
                from openagent.runtime.binary_store import make_binary_property

                file_name = _download_name(response, url)
                try:
                    prop = make_binary_property(
                        response.content, file_name=file_name, mime_type=content_type
                    )
                except ValueError as exc:
                    return NodeOutcome(
                        status="failed", error=str(exc), error_code="BINARY_TOO_LARGE"
                    )
                meta = {
                    "fileName": prop["fileName"],
                    "mimeType": prop["mimeType"],
                    "fileSize": prop["fileSize"],
                }
                outcome = NodeOutcome(
                    status="succeeded" if response.status_code < 400 else "failed",
                    outputs={
                        "status": response.status_code,
                        "ok": response.status_code < 400,
                        "headers": dict(response.headers),
                        **meta,
                    },
                    items=[{**item_envelope(meta), "binary": {"data": prop}}],
                )
            else:
                try:
                    parsed = response.json()
                except ValueError:
                    parsed = response.text[:20000]
                outcome = NodeOutcome(
                    status="succeeded" if response.status_code < 400 else "failed",
                    outputs={
                        "status": response.status_code,
                        "ok": response.status_code < 400,
                        "body": parsed,
                        "headers": dict(response.headers),
                    },
                )
            if response.status_code >= 400:
                outcome.error = f"HTTP {response.status_code}"
                outcome.error_code = "HTTP_ERROR"
            return outcome
        except httpx.HTTPError as exc:
            return NodeOutcome(status="failed", error=str(exc), error_code="HTTP_ERROR")

    if node_type == "approval":
        return NodeOutcome(
            status="waiting",
            outputs={"requires_approval": True, "message": _cfg(node, "message") or ""},
            reason="WAITING_FOR_APPROVAL",
            ports=[],
        )

    if node_type == "subworkflow":
        return await _exec_subworkflow(node, inputs, ctx)

    provider_nodes = {
        "agent": "an agent",
        "tool": "a registered tool",
        "connector_action": "a connector connection",
        "connector_trigger": "a connector connection",
        "connector_search": "a connector connection",
        "connector_resource": "a connector connection",
        "code_agent": "a coding workspace",
        "code_search": "a coding workspace",
        "code_read": "a coding workspace",
        "code_patch": "a coding workspace",
        "code_test": "a sandbox",
        "code_lint": "a sandbox",
        "code_review": "a coding workspace",
        "git_commit": "a coding workspace",
        "create_pr": "a git provider",
        "verify": "evaluation inputs",
        "evaluate": "evaluation inputs",
        "assert": "evaluation inputs",
        "quality_gate": "evaluation inputs",
        "retry": "evaluation inputs",
        "correct": "evaluation inputs",
    }
    needs = provider_nodes.get(node_type)
    if needs is not None:
        reason = f"SKIPPED_REQUIRES_{node_type.upper()}"
        return NodeOutcome(
            status="skipped",
            outputs={"skipped": True, "reason": reason, "requires": needs, "input": merged},
            reason=reason,
        )

    return NodeOutcome(
        status="failed",
        error=f"No local executor for node type '{node_type}'",
        error_code="NO_EXECUTOR",
    )


# ---------------------------------------------------------------------------
# Graph execution
# ---------------------------------------------------------------------------


def _edge_active(edge: Dict[str, Any], source_status: str, source_ports: List[str]) -> bool:
    # Typed edges carry the output port explicitly; legacy edges use `label`.
    label = str(edge.get("from_port") or edge.get("label") or "").strip().lower()
    if not label and str(edge.get("connection_type") or "").strip().lower() == "error":
        label = "error"
    if source_status == "failed":
        return label in ("failure", "error")
    if source_status == "waiting":
        return False
    if not label:
        return True
    if label in ("out", "output", "success"):
        return "out" in source_ports or source_status == "succeeded"
    return label in [p.lower() for p in source_ports]


async def execute_definition(
    definition: Dict[str, Any],
    trigger_input: Optional[Dict[str, Any]] = None,
    parent: Optional[RunContext] = None,
    *,
    model: Optional[ModelTarget] = None,
    token_sink: Optional[Callable[[str], Awaitable[None]]] = None,
    seed: Optional[Dict[str, Any]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
    """Run a definition. Returns (node_runs, output, overall_status).

    ``parent`` carries the sub-workflow plumbing (session, tenant, depth,
    call stack) when this definition is executed as a sub-workflow.

    ``seed`` resumes a parked run: its completed node runs, outputs, variables
    and trigger are restored and those nodes are not executed again. ``state``
    receives the final context so the caller can persist a resumable run.
    """
    trigger_input = trigger_input or {}
    seed = seed or {}
    triggers: List[Dict[str, Any]] = list(definition.get("triggers") or [])
    nodes: List[Dict[str, Any]] = list(definition.get("nodes") or [])
    edges: List[Dict[str, Any]] = list(definition.get("edges") or [])

    variables: Dict[str, Any] = {}
    for variable in definition.get("variables") or []:
        if isinstance(variable, dict) and variable.get("name"):
            variables[str(variable["name"])] = variable.get("default")

    ctx = RunContext(
        trigger=dict(seed.get("trigger") or {}),
        variables={**variables, **(seed.get("variables") or {})},
        outputs=dict(seed.get("outputs") or {}),
        db=parent.db if parent else None,
        organization_id=parent.organization_id if parent else None,
        depth=parent.depth if parent else 0,
        stack=parent.stack if parent else (),
        execution_id=parent.execution_id if parent else None,
        root_execution_id=parent.root_execution_id if parent else None,
        static_data=parent.static_data if parent else {},
        model=model or (parent.model if parent else None),
        token_sink=token_sink or (parent.token_sink if parent else None),
    )
    runs: List[Dict[str, Any]] = [dict(run) for run in (seed.get("runs") or [])]
    results: Dict[str, Dict[str, Any]] = {run["node_id"]: run for run in runs}

    def record(item: Dict[str, Any], status: str, outputs: Dict[str, Any], started: datetime,
               outcome_error: Optional[str] = None, error_code: Optional[str] = None,
               reason: Optional[str] = None, ports: Optional[List[str]] = None,
               continued: bool = False, attempt: int = 1,
               items: Optional[List[Dict[str, Any]]] = None,
               input_items_count: Optional[int] = None,
               handled: bool = False) -> None:
        completed = _now()
        run = {
            "node_id": str(item["id"]),
            "node_type": str(item.get("type") or "manual"),
            "node_name": str(item.get("name") or item.get("type") or "Node"),
            "status": status,
            "inputs": {},
            "outputs": outputs,
            "items": items if items is not None else [item_envelope(outputs)],
            "input_items": input_items_count if input_items_count is not None else 0,
            "output_items": len(items) if items is not None else 1,
            "error": outcome_error,
            "error_code": error_code,
            "reason": reason,
            "ports": ports or ["out"],
            "started_at": _iso(started),
            "completed_at": _iso(completed),
            "duration_ms": int((completed - started).total_seconds() * 1000),
            "attempt": attempt,
            "continued": continued,
            "handled": handled,
        }
        runs.append(run)
        results[run["node_id"]] = run

    # Triggers run first and seed the context.
    if not triggers:
        triggers = [{"id": "trigger", "type": "manual", "name": "Manual"}]
        trigger_input = trigger_input or {}
    for trigger in triggers:
        started = _now()
        outcome = await _exec_trigger(trigger, trigger_input, ctx)
        record(trigger, outcome.status, outcome.outputs, started, items=outcome.items)

    incoming: Dict[str, List[Dict[str, Any]]] = {str(n["id"]): [] for n in nodes}
    outgoing: Dict[str, List[Dict[str, Any]]] = {str(t["id"]): [] for t in triggers}
    outgoing.update({str(n["id"]): [] for n in nodes})
    for edge in edges:
        source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
        if target in incoming and source in outgoing:
            incoming[target].append(edge)
            outgoing[source].append(edge)

    pending = [n for n in nodes if str(n["id"]) not in results]
    guard = 0
    while pending and guard <= len(nodes) + 1:
        guard += 1
        progressed = False
        for node in list(pending):
            node_id = str(node["id"])
            deps = incoming[node_id]
            if not deps:
                started = _now()
                record(node, "skipped", {}, started, reason="UNREACHABLE",
                       outcome_error="Node has no incoming connection")
                pending.remove(node)
                progressed = True
                continue
            if any(str(edge["from"]) not in results for edge in deps):
                continue

            def _upstream(edge: Dict[str, Any]) -> Dict[str, Any]:
                return results[str(edge["from"])]

            def _edge_runs(edge: Dict[str, Any]) -> bool:
                upstream = _upstream(edge)
                status = upstream["status"]
                ports = upstream["ports"]
                if status == "failed" and upstream.get("continued"):
                    # "Continue on error" passes the input down the success path.
                    status, ports = "succeeded", ["out"]
                if not _edge_active(edge, status, ports):
                    return False
                # n8n branch conditions: an edge may require success/failure
                # and/or its own expression.
                condition = edge.get("condition")
                if isinstance(condition, dict):
                    when = str(condition.get("when") or "always")
                    if when == "success" and status != "succeeded":
                        return False
                    if when == "failure" and status != "failed":
                        return False
                    if condition.get("expression"):
                        try:
                            return evaluate(condition["expression"], ctx)
                        except Exception:  # a broken condition must not crash the run
                            return False
                return True

            active = [edge for edge in deps if _edge_runs(edge)]
            started = _now()
            if not active:
                record(node, "skipped", {}, started, reason="BRANCH_NOT_TAKEN")
                pending.remove(node)
                progressed = True
                continue

            inputs: Dict[str, Any] = {}
            incoming_items: List[Dict[str, Any]] = []
            for edge in active:
                # Fan-in (Merge) reaches the same node through several edges:
                # keep every branch instead of letting the last one win.
                label = str(edge.get("to_port") or edge.get("label") or "input")
                if label in inputs:
                    label = f"{label}:{edge['from']}"
                upstream = results[str(edge["from"])]
                inputs[label] = upstream["outputs"]
                incoming_items.extend(upstream.get("items") or [])
            if not incoming_items:
                incoming_items = _input_items(inputs)

            if node.get("disabled"):
                record(node, "skipped", {}, started, reason="DISABLED")
                pending.remove(node)
                progressed = True
                continue

            outcome, attempts, continued = await _execute_with_policy(
                node, inputs, ctx, items=incoming_items
            )
            record(node, outcome.status, outcome.outputs, started, outcome.error,
                   outcome.error_code, outcome.reason, outcome.ports,
                   continued=continued, attempt=attempts, items=outcome.items,
                   input_items_count=len(incoming_items), handled=outcome.handled)
            ctx.outputs[node_id] = {
                **outcome.outputs,
                "_status": outcome.status,
                "_error": outcome.error,
            }
            runs[-1]["inputs"] = inputs
            pending.remove(node)
            progressed = True

        if not progressed:
            break

    for node in pending:
        record(node, "skipped", {}, _now(), reason="UNREACHABLE",
               outcome_error="Upstream dependency did not run")

    statuses = [run["status"] for run in runs]
    real_failures = [
        run for run in runs
        if run["status"] == "failed"
        and not run.get("continued")
        and not run.get("handled")
    ]
    if "waiting" in statuses:
        overall = "waiting"
    elif real_failures:
        overall = "failed"
    else:
        overall = "completed"

    terminal_outputs: Dict[str, Any] = {}
    for run in runs:
        node_id = run["node_id"]
        if not outgoing.get(node_id):
            terminal_outputs[node_id] = run["outputs"]
    final_output = {
        "status": overall,
        "nodes": {run["node_id"]: run["outputs"] for run in runs},
        "terminal": terminal_outputs,
    }
    if state is not None:
        state.update(
            {
                "trigger": ctx.trigger,
                "variables": ctx.variables,
                "outputs": ctx.outputs,
                "static_data": ctx.static_data,
            }
        )
    return runs, final_output, overall


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _trigger_type_for(definition: Dict[str, Any]) -> WorkflowExecutionTriggerType:
    triggers = definition.get("triggers") or []
    ttype = str((triggers[0] or {}).get("type") if triggers else "manual")
    return {
        "webhook": WorkflowExecutionTriggerType.WEBHOOK,
        "schedule": WorkflowExecutionTriggerType.SCHEDULED,
        "event": WorkflowExecutionTriggerType.EVENT,
        "chat": WorkflowExecutionTriggerType.API,
        "execute_workflow": WorkflowExecutionTriggerType.API,
        "manual": WorkflowExecutionTriggerType.MANUAL,
    }.get(ttype, WorkflowExecutionTriggerType.API)


async def _run_error_workflow(
    db: AsyncSession,
    *,
    workflow: Workflow,
    organization_id: UUID,
    definition: Dict[str, Any],
    execution: WorkflowExecution,
    node_runs: List[Dict[str, Any]],
    triggered_by: Optional[UUID],
) -> Optional[Dict[str, Any]]:
    """Run the workflow configured as this workflow's "On error" handler.

    Mirrors n8n's workflow settings panel: ``settings.error_workflow_id`` (the
    1.1 top-level ``error_workflow_id`` is accepted too). The handler receives
    ``{execution, workflow, error, lastNodeExecuted}`` and never triggers its
    own error workflow, so a failure loop cannot form.
    """
    settings = definition.get("settings") or {}
    raw = settings.get("error_workflow_id") or definition.get("error_workflow_id")
    if not raw or str(raw).strip() in ("", str(workflow.id)):
        return None
    try:
        target_id = UUID(str(raw).strip())
    except (TypeError, ValueError):
        return {
            "workflow_id": str(raw),
            "status": "invalid",
            "error": "error_workflow_id is not a workflow id",
        }

    target = (
        await db.execute(
            select(Workflow).where(
                Workflow.id == target_id,
                Workflow.deleted_at.is_(None),
                Workflow.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        return {
            "workflow_id": str(target_id),
            "status": "missing",
            "error": "Error workflow was not found in this organization",
        }

    last = next(
        (
            run
            for run in reversed(node_runs)
            if run["status"] == "failed"
            and not run.get("continued")
            and not run.get("handled")
        ),
        None,
    )
    payload: Dict[str, Any] = {
        "execution": {
            "id": str(execution.id),
            "workflow_id": str(workflow.id),
            "status": "failed",
            "error": execution.error_message,
            "trigger_type": getattr(execution.trigger_type, "value", execution.trigger_type),
        },
        "workflow": {"id": str(workflow.id), "name": workflow.name},
        "error": {
            "message": execution.error_message,
            "code": execution.error_code,
            "node": (last or {}).get("node_name"),
        },
        "lastNodeExecuted": {
            "node_id": (last or {}).get("node_id"),
            "node_name": (last or {}).get("node_name"),
            "error": (last or {}).get("error"),
            "error_code": (last or {}).get("error_code"),
        },
    }

    try:
        run = await run_workflow(
            db,
            workflow=target,
            organization_id=organization_id,
            triggered_by=triggered_by,
            trigger_input=payload,
            trigger_error_workflow=False,
        )
    except Exception as exc:  # a broken handler must not hide the first failure
        return {"workflow_id": str(target_id), "status": "failed", "error": str(exc)}

    return {
        "workflow_id": str(target_id),
        "workflow_name": target.name,
        "execution_id": run.get("execution_id"),
        "status": run.get("status"),
    }


def _queued_result(
    execution: WorkflowExecution, workflow: Workflow, message: str
) -> Dict[str, Any]:
    """The run response for an execution that was persisted, not executed."""
    return {
        "execution_id": str(execution.id),
        "workflow_id": str(workflow.id),
        "status": "queued",
        "message": message,
        "node_runs": [],
        "output": {},
        "summary": {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "waiting": 0,
            "warnings": 0,
            "duration_ms": 0,
            "items_pruned": 0,
        },
        "error_message": None,
    }


async def run_workflow(
    db: AsyncSession,
    *,
    workflow: Workflow,
    organization_id: UUID,
    triggered_by: Optional[UUID],
    definition: Optional[Dict[str, Any]] = None,
    trigger_input: Optional[Dict[str, Any]] = None,
    version_label: Optional[str] = None,
    model: Optional[ModelTarget] = None,
    token_sink: Optional[Callable[[str], Awaitable[None]]] = None,
    resume_from: Optional[WorkflowExecution] = None,
    trigger_error_workflow: bool = True,
    parent_execution_id: Optional[UUID] = None,
    root_execution_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Create an execution row, run the definition, persist node runs.

    ``resume_from`` continues a parked execution (n8n's Wait node) instead of
    creating a new row: completed nodes are restored from the saved state and
    the waiting node is retried.
    """
    seed: Dict[str, Any] = {}
    state: Dict[str, Any] = {}
    seeded_ids: set[str] = set()
    if resume_from is not None:
        saved = (resume_from.metadata or {}).get("wait") or {}
        seed = {
            "runs": saved.get("runs") or [],
            "outputs": saved.get("outputs") or {},
            "variables": saved.get("variables") or {},
            "trigger": saved.get("trigger") or {},
        }
        seeded_ids = {str(run.get("node_id")) for run in seed["runs"]}
        if definition is None:
            definition = saved.get("definition")
        if trigger_input is None:
            trigger_input = saved.get("trigger_input") or {}

    definition_override = definition is not None and resume_from is None
    version: Optional[WorkflowVersion] = None
    if resume_from is not None:
        # A resume keeps the version it started with; no lookup is needed.
        version = None
    elif version_label:
        result = await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == version_label,
            )
        )
        version = result.scalar_one_or_none()
    if version is None and resume_from is None:
        result = await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.created_at.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()

    if definition is None:
        if version is None:
            raise ValueError("Workflow has no saved version to run")
        definition = version.definition or {}

    # ---- workflow static data (n8n ``$getWorkflowStaticData``) ------------
    # The version's static_data is the seed; the mutable copy on the workflow
    # row wins, and whatever the run leaves behind is written back below.
    static_data: Dict[str, Any] = {}
    seeded_static = definition.get("static_data")
    if isinstance(seeded_static, dict):
        static_data.update(json.loads(json.dumps(seeded_static, default=str)))
    stored_static = getattr(workflow, "static_data", None)
    if isinstance(stored_static, dict):
        static_data.update(json.loads(json.dumps(stored_static, default=str)))

    # ---- per-workflow concurrency (n8n's workflow "Concurrency" field) -----
    # Opt-in: without settings.max_concurrency every run starts immediately.
    # Over the limit the run is persisted as queued (never dropped) and the
    # scheduler's dispatcher starts it when a slot frees up.
    # ---- queue mode / graceful drain (Phase 12) --------------------------
    # With WORKFLOW_QUEUE_MODE on, or while the API is draining, the main
    # process never executes: it persists the run and a worker claims it.
    if resume_from is None and (queue_mode_enabled() or is_shutting_down()):
        queued_execution = WorkflowExecution(
            organization_id=organization_id,
            workflow_id=workflow.id,
            workflow_version_id=version.id if version else None,
            status=WorkflowExecutionStatus.QUEUED,
            trigger_type=_trigger_type_for(definition),
            started_at=None,
            metadata={
                "run_source": "draft" if definition_override else "version",
                "trigger_input": trigger_input or {},
                "queue_mode": "worker",
                "queued_at": _now().isoformat(),
                "queued": {
                    "definition": definition,
                    "trigger_input": trigger_input or {},
                    "mode": "worker",
                },
            },
        )
        db.add(queued_execution)
        await db.flush()
        await db.commit()
        reason = (
            "Queue mode is on" if queue_mode_enabled() else "The API is shutting down"
        )
        return _queued_result(
            queued_execution, workflow, f"{reason}; run queued for a worker"
        )

    limit = concurrency_limit(definition) if resume_from is None else 0
    if limit and await active_executions(db, workflow.id) >= limit:
        queued_execution = WorkflowExecution(
            organization_id=organization_id,
            workflow_id=workflow.id,
            workflow_version_id=version.id if version else None,
            status=WorkflowExecutionStatus.QUEUED,
            trigger_type=_trigger_type_for(definition),
            started_at=None,
            metadata={
                "run_source": "draft" if definition_override else "version",
                "trigger_input": trigger_input or {},
                "queued_at": _now().isoformat(),
                "queued": {
                    "definition": definition,
                    "trigger_input": trigger_input or {},
                    "limit": limit,
                },
            },
        )
        db.add(queued_execution)
        await db.flush()
        await db.commit()
        return _queued_result(
            queued_execution,
            workflow,
            f"Workflow concurrency limit ({limit}) reached; run queued",
        )

    started_at = _now()
    if resume_from is not None:
        execution = resume_from
        execution.status = WorkflowExecutionStatus.RUNNING
        execution.completed_at = None
        execution.started_at = execution.started_at or started_at
        execution_id = execution.id
    else:
        execution = WorkflowExecution(
            organization_id=organization_id,
            workflow_id=workflow.id,
            workflow_version_id=version.id if version else None,
            status=WorkflowExecutionStatus.RUNNING,
            trigger_type=_trigger_type_for(definition),
            started_at=started_at,
            parent_execution_id=parent_execution_id,
            root_execution_id=root_execution_id,
            metadata={
                "run_source": "draft" if definition_override else "version",
                "trigger_input": trigger_input or {},
            },
        )
        db.add(execution)
        await db.flush()
        execution_id = execution.id

    # A resumed run keeps its tree; a new run is its own root unless it was
    # started by a parent (sub-workflow call).
    if execution.root_execution_id is None:
        execution.root_execution_id = execution_id
    if execution.parent_execution_id is not None:
        execution.metadata = {
            **(execution.metadata or {}),
            "parent_execution_id": str(execution.parent_execution_id),
        }

    # Crash journal + drain accounting. The row is committed before the engine
    # starts so a process that dies mid-run leaves a visible `running` row the
    # journal sweep can mark `crashed` (instead of nothing at all).
    journal_start(execution)
    await db.commit()
    begin_run(execution_id)

    budget = execution_timeout(definition)
    try:
        runner = execute_definition(
            definition,
            trigger_input or {},
            parent=RunContext(
                db=db,
                organization_id=organization_id,
                depth=0,
                stack=(str(workflow.id),),
                execution_id=execution_id,
                root_execution_id=execution.root_execution_id or execution_id,
                static_data=static_data,
            ),
            model=model,
            token_sink=token_sink,
            seed=seed,
            state=state,
        )
        if budget:
            node_runs, output, overall = await asyncio.wait_for(runner, timeout=float(budget))
        else:
            node_runs, output, overall = await runner
    except asyncio.TimeoutError:
        node_runs = []
        output = {"status": "failed", "error": f"Execution exceeded {budget}s"}
        overall = "failed"
        execution.error_message = f"Execution exceeded the {budget}s workflow budget"
        execution.error_code = "EXECUTION_TIMEOUT"
    except Exception as exc:  # engine failure is reported, never swallowed
        node_runs, output, overall = [], {"status": "failed", "error": str(exc)}, "failed"
        execution.error_message = str(exc)
        execution.error_code = "EXECUTION_ENGINE_ERROR"
    finally:
        end_run(execution_id)

    completed_at = _now()
    failed = [run for run in node_runs if run["status"] == "failed" and not run.get("continued")]
    continued_failures = [run for run in node_runs if run["status"] == "failed" and run.get("continued")]
    status_map = {
        "completed": WorkflowExecutionStatus.COMPLETED,
        "failed": WorkflowExecutionStatus.FAILED,
        "waiting": WorkflowExecutionStatus.WAITING,
    }
    run_started = execution.started_at or started_at
    wait_info: Optional[Dict[str, Any]] = None
    if overall == "waiting":
        wait_run = next((run for run in node_runs if run["status"] == "waiting"), None)
        wait_outputs = (wait_run or {}).get("outputs") or {}
        resume_at = wait_outputs.get("resume_at")
        waits_for_webhook = str(wait_outputs.get("resume") or "").lower() == "webhook"
        if wait_run is not None and (resume_at or waits_for_webhook):
            # The wait node is stored as completed: on resume the scheduler
            # does not make the workflow wait for the same duration again.
            waited = {
                **wait_run,
                "status": "succeeded",
                "reason": None,
                "outputs": {
                    **(wait_run.get("outputs") or {}),
                    "waiting": False,
                    "waited_seconds": (wait_run.get("outputs") or {}).get("requested_seconds", 0.0),
                },
                "completed_at": _iso(completed_at),
                "resumed": True,
            }
            wait_info = {
                "node_id": wait_run["node_id"],
                "resume_at": resume_at,
                # A webhook wait has no time; the caller resumes it with this
                # single-run token through POST .../webhook-resume.
                "webhook_token": secrets.token_urlsafe(32) if waits_for_webhook else None,
                "definition": definition,
                "trigger_input": trigger_input or {},
                "trigger": state.get("trigger") or {},
                "variables": state.get("variables") or {},
                "outputs": state.get("outputs") or {},
                "runs": [
                run
                for run in node_runs
                if run["node_id"] != wait_run["node_id"] and run["status"] != "skipped"
            ]
                + [waited],
            }

    execution.status = status_map[overall]
    execution.completed_at = completed_at
    # Persist a bounded item view per node (execution_data mirrors n8n's shape
    # and the run panel reads it) so a huge list cannot bloat the row.
    # n8n's "Save execution data": statuses and events are always stored, the
    # per-node items only as far as the workflow's setting allows.
    save_mode = save_execution_data_mode(definition)
    store_items = save_mode == "all" or (save_mode == "on_error" and overall == "failed")
    persisted_runs: List[Dict[str, Any]] = []
    execution_data: Dict[str, List[Dict[str, Any]]] = {}
    pruned_items = 0
    for run in node_runs:
        saved = dict(run)
        if store_items:
            stored, dropped = _persisted_items(run.get("items"))
            pruned_items += dropped
            saved["items"] = stored
            saved["items_truncated"] = dropped
            execution_data[run["node_id"]] = stored
        else:
            saved["items"] = []
            saved["items_truncated"] = len(run.get("items") or [])
            saved.pop("inputs", None)
            saved.pop("outputs", None)
        persisted_runs.append(saved)

    metadata = {
        **(execution.metadata or {}),
        "node_runs": persisted_runs,
        "output": output,
        "save_execution_data": save_mode,
        "execution_data": {"nodes": execution_data},
        "summary": {
            "total": len(node_runs),
            "succeeded": sum(1 for run in node_runs if run["status"] == "succeeded"),
            "failed": len(failed),
            "skipped": sum(1 for run in node_runs if run["status"] == "skipped"),
            "waiting": sum(1 for run in node_runs if run["status"] == "waiting"),
            "warnings": len(continued_failures),
            "duration_ms": int((completed_at - run_started).total_seconds() * 1000),
            "items_pruned": pruned_items,
        },
    }
    # Persist whatever the run left in workflow static data; a version's
    # authored seed never overwrites the mutable value on the row.
    if isinstance(state.get("static_data"), dict):
        workflow.static_data = json.loads(
            json.dumps(state["static_data"], default=str)
        )

    metadata.pop("wait", None)
    if wait_info is not None:
        metadata["wait"] = wait_info
    execution.metadata = metadata
    if failed:
        execution.error_code = "NODE_FAILED"
        execution.error_message = "; ".join(
            f"{run['node_name']}: {run['error']}" for run in failed if run.get("error")
        )[:2000] or "One or more nodes failed"

    sequence = 1
    events: List[ExecutionEvent] = []
    events.append(ExecutionEvent(
        organization_id=organization_id,
        workflow_execution_id=execution_id,
        event_type="execution.started",
        sequence=sequence,
        timestamp=started_at,
        payload={"workflow_id": str(workflow.id), "run_source": execution.metadata.get("run_source")},
    ))
    for run in node_runs:
        if run["node_id"] in seeded_ids:
            continue  # already recorded by the run that parked
        sequence += 1
        events.append(ExecutionEvent(
            organization_id=organization_id,
            workflow_execution_id=execution_id,
            event_type=f"node.{run['status']}",
            sequence=sequence,
            timestamp=_now(),
            payload={
                "node_id": run["node_id"],
                "node_type": run["node_type"],
                "duration_ms": run["duration_ms"],
                "error": run["error"],
            },
        ))
    sequence += 1
    events.append(ExecutionEvent(
        organization_id=organization_id,
        workflow_execution_id=execution_id,
        event_type=f"execution.{overall}",
        sequence=sequence,
        timestamp=completed_at,
        payload={"duration_ms": execution.metadata["summary"]["duration_ms"]},
    ))
    db.add_all(events)
    await db.commit()

    if overall == "failed" and trigger_error_workflow:
        error_run = await _run_error_workflow(
            db,
            workflow=workflow,
            organization_id=organization_id,
            definition=definition,
            execution=execution,
            node_runs=node_runs,
            triggered_by=triggered_by,
        )
        if error_run is not None:
            execution.metadata = {**(execution.metadata or {}), "error_workflow": error_run}
            await db.commit()

    return {
        "execution_id": str(execution_id),
        "workflow_id": str(workflow.id),
        "status": overall,
        "message": {
            "completed": "Workflow run completed",
            "failed": "Workflow run failed",
            "waiting": "Workflow run is waiting (approval or scheduled resume)",
        }[overall],
        "node_runs": node_runs,
        "output": output,
        "summary": execution.metadata["summary"],
        "error_message": execution.error_message,
    }


async def run_single_node(
    definition: Dict[str, Any],
    node_id: str,
    trigger_input: Optional[Dict[str, Any]] = None,
    db: Optional[AsyncSession] = None,
    organization_id: Optional[UUID] = None,
    model: Optional[ModelTarget] = None,
    *,
    use_pinned: bool = False,
    upstream: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute one node (or trigger) of a definition — n8n's "Execute step".

    ``use_pinned`` is a **preview-only** switch: pinned node output from
    ``settings.pinned_data`` stands in for nodes that have not run, both as
    ``{{nodes.<id>.*}}`` data and as this node's input items. ``upstream`` does
    the same for caller-supplied output (partial execution). Real runs never
    pass either, so pins can never leak into production data.

    Retries, timeouts and "continue on error" apply exactly as in a full run.
    """
    trigger_input = trigger_input or {}
    triggers: List[Dict[str, Any]] = list(definition.get("triggers") or [])
    nodes: List[Dict[str, Any]] = list(definition.get("nodes") or [])
    edges: List[Dict[str, Any]] = [e for e in (definition.get("edges") or []) if isinstance(e, dict)]

    variables: Dict[str, Any] = {}
    for variable in definition.get("variables") or []:
        if isinstance(variable, dict) and variable.get("name"):
            variables[str(variable["name"])] = variable.get("default")

    ctx = RunContext(
        trigger={}, variables=variables, db=db, organization_id=organization_id, model=model
    )
    if triggers:
        await _exec_trigger(triggers[0], trigger_input, ctx)
    else:
        ctx.trigger.update(trigger_input)

    pinned = (definition.get("settings") or {}).get("pinned_data") or {}
    if not isinstance(pinned, dict):
        pinned = {}
    supplied = upstream or {}

    def _available(ref: str) -> Optional[Dict[str, Any]]:
        """Output a source node would have produced, if it is available."""
        if ref in supplied:
            value = supplied[ref]
            return value if isinstance(value, dict) else {"items": as_items(value)}
        if use_pinned and ref in pinned:
            datum = pinned[ref]
            outputs = datum.get("outputs") if isinstance(datum, dict) else None
            if isinstance(outputs, dict):
                return outputs
            if outputs is not None:
                return {"items": as_items(outputs)}
        return None

    used_sources: List[str] = []
    if use_pinned:
        for ref, datum in pinned.items():
            outputs = datum.get("outputs") if isinstance(datum, dict) else None
            if isinstance(outputs, dict):
                ctx.outputs[str(ref)] = {**outputs, "_status": "succeeded", "_error": None}
                used_sources.append(str(ref))

    started = _now()
    target_trigger = next((t for t in triggers if str(t.get("id")) == node_id), None)
    if target_trigger is not None:
        outcome = await _exec_trigger(target_trigger, trigger_input, ctx)
        attempts, continued = 1, False
        item = target_trigger
        inputs: Dict[str, Any] = {"input": trigger_input}
    else:
        node = next((n for n in nodes if str(n.get("id")) == node_id), None)
        if node is None:
            raise ValueError(f"Node {node_id!r} is not part of this workflow")
        item = node
        inputs: Dict[str, Any] = {}
        incoming_items: List[Dict[str, Any]] = []
        for edge in edges:
            if str(edge.get("to") or "") != str(node.get("id") or ""):
                continue
            source = str(edge.get("from") or "")
            outputs = _available(source)
            if outputs is None:
                continue
            label = str(edge.get("label") or edge.get("from_port") or source or "input")
            inputs[label] = outputs
            incoming_items.extend(as_items(outputs.get("items") if "items" in outputs else outputs))
            used_sources.append(source)
        if not incoming_items and trigger_input:
            incoming_items = as_items(trigger_input)
        if not inputs:
            inputs = {"input": trigger_input}
        if node.get("disabled"):
            completed = _now()
            return {
                "node_id": str(node.get("id")),
                "node_type": str(node.get("type") or ""),
                "node_name": str(node.get("name") or node.get("type") or "Node"),
                "status": "skipped",
                "inputs": inputs,
                "outputs": {},
                "error": None,
                "error_code": None,
                "reason": "DISABLED",
                "duration_ms": int((completed - started).total_seconds() * 1000),
                "attempt": 1,
                "continued": False,
            }
        outcome, attempts, continued = await _execute_with_policy(
            node, inputs, ctx, items=incoming_items
        )

    completed = _now()
    return {
        "node_id": str(item.get("id")),
        "node_type": str(item.get("type") or ""),
        "node_name": str(item.get("name") or item.get("type") or "Node"),
        "status": outcome.status,
        "inputs": inputs,
        "outputs": outcome.outputs,
        "items": outcome.items if outcome.items is not None else as_items(outcome.outputs),
        "sources": sorted(set(used_sources)),
        "error": outcome.error,
        "error_code": outcome.error_code,
        "reason": outcome.reason,
        "duration_ms": int((completed - started).total_seconds() * 1000),
        "attempt": attempts,
        "continued": continued,
        "handled": outcome.handled,
    }
