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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SYNC_DELAY_SECONDS = 10.0
HTTP_NODE_TIMEOUT_SECONDS = 20.0
MAX_LOOP_ITEMS = 1000
MAX_SUBWORKFLOW_DEPTH = 5

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
    # Sub-workflow plumbing (n8n "Execute Workflow"): the DB session used to
    # resolve the child definition, the tenant it must belong to, the current
    # nesting depth and the call stack used to reject cycles.
    db: Any = None
    organization_id: Optional[UUID] = None
    depth: int = 0
    stack: Tuple[str, ...] = ()

    def child(self, item: Any, index: int) -> "RunContext":
        return RunContext(
            trigger=self.trigger,
            variables=self.variables,
            outputs=self.outputs,
            item=item,
            index=index,
            db=self.db,
            organization_id=self.organization_id,
            depth=self.depth,
            stack=self.stack,
        )


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


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


def resolve(value: Any, ctx: RunContext) -> Any:
    """Resolve ``{{ ... }}`` expressions in any nested config value."""
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

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


def _cfg(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    config = node.get("config") or {}
    return config.get(key, default)


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
    node: Dict[str, Any], inputs: Dict[str, Any], ctx: "RunContext"
) -> Tuple["NodeOutcome", int, bool]:
    """Run a node honouring retries, timeout and "continue on error" settings.

    Returns (outcome, attempts_used, continued). ``continued`` is True when the
    node failed but the node's ``config.on_error`` is "continue", in which case
    the failure is recorded and the input is passed downstream.
    """
    policy = node.get("retry_policy") or {}
    try:
        extra_attempts = int(policy.get("max_attempts") or 0)
    except (TypeError, ValueError):
        extra_attempts = 0
    attempts_allowed = max(1, min(extra_attempts + 1, 11))

    raw_timeout = node.get("timeout_seconds")
    try:
        timeout = max(1.0, min(float(raw_timeout), 300.0)) if raw_timeout else None
    except (TypeError, ValueError):
        timeout = None

    continue_on_error = str(_cfg(node, "on_error") or "").strip().lower() == "continue"

    outcome = NodeOutcome(status="failed", error="Node was not executed", error_code="UNKNOWN")
    attempts_used = 0
    for attempt in range(1, attempts_allowed + 1):
        attempts_used = attempt
        try:
            if timeout is not None:
                outcome = await asyncio.wait_for(_exec_node(node, inputs, ctx), timeout=timeout)
            else:
                outcome = await _exec_node(node, inputs, ctx)
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
            await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))

    if outcome.status == "failed" and continue_on_error:
        passthrough = _merged_input(inputs)
        outcome.outputs = passthrough if isinstance(passthrough, dict) else {"items": passthrough}
        return outcome, attempts_used, True

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
    if ttype == "webhook":
        payload = {
            "method": "POST",
            "headers": {},
            "body": trigger_input,
            "query": {},
            "path": config.get("path") or "",
            **trigger_input,
        }
    elif ttype == "schedule":
        payload = {"fired_at": _now().isoformat(), "cron": config.get("cron"), **trigger_input}
    elif ttype == "event":
        payload = {"event": config.get("event"), "payload": trigger_input, **trigger_input}
    else:
        payload = dict(trigger_input)

    payload["input"] = trigger_input
    ctx.trigger.update(payload)
    return NodeOutcome(status="succeeded", outputs=payload)


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

    payload = resolve(_cfg(node, "input"), ctx)
    if payload is None:
        payload = _merged_input(inputs)
    if not isinstance(payload, dict):
        payload = {"input": payload}

    parent = RunContext(
        db=ctx.db,
        organization_id=ctx.organization_id,
        depth=ctx.depth + 1,
        stack=ctx.stack + (str(target_id),),
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
    if failed:
        return NodeOutcome(
            status="failed",
            outputs=outputs,
            error="; ".join(f"{r['node_name']}: {r['error']}" for r in failed if r.get("error"))[:1000]
            or f"Subworkflow '{target.name}' failed",
            error_code="SUBWORKFLOW_FAILED",
        )
    return NodeOutcome(status="succeeded", outputs=outputs)


async def _exec_node(node: Dict[str, Any], inputs: Dict[str, Any], ctx: RunContext) -> NodeOutcome:
    node_type = str(node.get("type") or "")
    merged = _merged_input(inputs)

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
        out: List[Any] = []
        for index, item in enumerate(items[:MAX_LOOP_ITEMS]):
            item_ctx = ctx.child(item, index)
            if node_type == "filter":
                if evaluate(expression, item_ctx):
                    out.append(item)
            else:
                out.append(resolve(expression, item_ctx))
        return NodeOutcome(status="succeeded", outputs={"items": out, "count": len(out)})

    if node_type in ("sort", "limit", "remove_duplicates", "split_out"):
        items = _as_list(_items_for(node, inputs, ctx))

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

            ordered = sorted(items[:MAX_LOOP_ITEMS], key=sort_key, reverse=descending)
            return NodeOutcome(status="succeeded", outputs={"items": ordered, "count": len(ordered)})

        if node_type == "limit":
            try:
                maximum = int(_as_number(_cfg(node, "max_items")) or 10)
            except (TypeError, ValueError):
                maximum = 10
            maximum = max(0, min(maximum, MAX_LOOP_ITEMS))
            keep = str(_cfg(node, "keep") or "first").strip().lower()
            kept = items[-maximum:] if keep == "last" and maximum else items[:maximum]
            return NodeOutcome(status="succeeded", outputs={"items": kept, "count": len(kept)})

        if node_type == "remove_duplicates":
            key = str(_cfg(node, "key") or "").strip()
            seen: set[str] = set()
            unique: List[Any] = []
            for item in items[:MAX_LOOP_ITEMS]:
                value = _field_value(item, key) if key else item
                marker = json.dumps(value, sort_keys=True, default=str)
                if marker in seen:
                    continue
                seen.add(marker)
                unique.append(item)
            return NodeOutcome(
                status="succeeded",
                outputs={
                    "items": unique,
                    "count": len(unique),
                    "duplicates_removed": len(items) - len(unique),
                },
            )

        # split_out
        field = str(_cfg(node, "field") or "").strip()
        if not field:
            return NodeOutcome(
                status="failed",
                error="Split out node needs a field to split on",
                error_code="INVALID_CONFIG",
            )
        split: List[Any] = []
        for item in items[:MAX_LOOP_ITEMS]:
            value = _field_value(item, field)
            if isinstance(value, list):
                split.extend(value)
            elif value is None:
                split.append(item)
            else:
                split.append(value)
        return NodeOutcome(status="succeeded", outputs={"items": split, "count": len(split)})

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
        return NodeOutcome(status="succeeded", outputs={"merged": merged})

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
        "prompt": "a model provider",
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
    label = str(edge.get("label") or "").strip().lower()
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
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
    """Run a definition. Returns (node_runs, output, overall_status).

    ``parent`` carries the sub-workflow plumbing (session, tenant, depth,
    call stack) when this definition is executed as a sub-workflow.
    """
    trigger_input = trigger_input or {}
    triggers: List[Dict[str, Any]] = list(definition.get("triggers") or [])
    nodes: List[Dict[str, Any]] = list(definition.get("nodes") or [])
    edges: List[Dict[str, Any]] = list(definition.get("edges") or [])

    variables: Dict[str, Any] = {}
    for variable in definition.get("variables") or []:
        if isinstance(variable, dict) and variable.get("name"):
            variables[str(variable["name"])] = variable.get("default")

    ctx = RunContext(
        trigger={},
        variables=variables,
        db=parent.db if parent else None,
        organization_id=parent.organization_id if parent else None,
        depth=parent.depth if parent else 0,
        stack=parent.stack if parent else (),
    )
    runs: List[Dict[str, Any]] = []
    results: Dict[str, Dict[str, Any]] = {}

    def record(item: Dict[str, Any], status: str, outputs: Dict[str, Any], started: datetime,
               outcome_error: Optional[str] = None, error_code: Optional[str] = None,
               reason: Optional[str] = None, ports: Optional[List[str]] = None,
               continued: bool = False, attempt: int = 1) -> None:
        completed = _now()
        run = {
            "node_id": str(item["id"]),
            "node_type": str(item.get("type") or "manual"),
            "node_name": str(item.get("name") or item.get("type") or "Node"),
            "status": status,
            "inputs": {},
            "outputs": outputs,
            "error": outcome_error,
            "error_code": error_code,
            "reason": reason,
            "ports": ports or ["out"],
            "started_at": _iso(started),
            "completed_at": _iso(completed),
            "duration_ms": int((completed - started).total_seconds() * 1000),
            "attempt": attempt,
            "continued": continued,
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
        record(trigger, outcome.status, outcome.outputs, started)

    incoming: Dict[str, List[Dict[str, Any]]] = {str(n["id"]): [] for n in nodes}
    outgoing: Dict[str, List[Dict[str, Any]]] = {str(t["id"]): [] for t in triggers}
    outgoing.update({str(n["id"]): [] for n in nodes})
    for edge in edges:
        source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
        if target in incoming and source in outgoing:
            incoming[target].append(edge)
            outgoing[source].append(edge)

    pending = [n for n in nodes]
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
                return _edge_active(edge, status, ports)

            active = [edge for edge in deps if _edge_runs(edge)]
            started = _now()
            if not active:
                record(node, "skipped", {}, started, reason="BRANCH_NOT_TAKEN")
                pending.remove(node)
                progressed = True
                continue

            inputs: Dict[str, Any] = {}
            for edge in active:
                label = str(edge.get("label") or "input")
                inputs[label] = results[str(edge["from"])]["outputs"]

            if node.get("disabled"):
                record(node, "skipped", {}, started, reason="DISABLED")
                pending.remove(node)
                progressed = True
                continue

            outcome, attempts, continued = await _execute_with_policy(node, inputs, ctx)
            record(node, outcome.status, outcome.outputs, started, outcome.error,
                   outcome.error_code, outcome.reason, outcome.ports,
                   continued=continued, attempt=attempts)
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
    real_failures = [run for run in runs if run["status"] == "failed" and not run.get("continued")]
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
        "manual": WorkflowExecutionTriggerType.MANUAL,
    }.get(ttype, WorkflowExecutionTriggerType.API)


async def run_workflow(
    db: AsyncSession,
    *,
    workflow: Workflow,
    organization_id: UUID,
    triggered_by: Optional[UUID],
    definition: Optional[Dict[str, Any]] = None,
    trigger_input: Optional[Dict[str, Any]] = None,
    version_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an execution row, run the definition, persist node runs."""
    definition_override = definition is not None
    version: Optional[WorkflowVersion] = None
    if version_label:
        result = await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == version_label,
            )
        )
        version = result.scalar_one_or_none()
    if version is None:
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

    started_at = _now()
    execution = WorkflowExecution(
        organization_id=organization_id,
        workflow_id=workflow.id,
        workflow_version_id=version.id if version else None,
        status=WorkflowExecutionStatus.RUNNING,
        trigger_type=_trigger_type_for(definition),
        started_at=started_at,
        metadata={"run_source": "draft" if definition_override else "version"},
    )
    db.add(execution)
    await db.flush()
    execution_id = execution.id

    try:
        node_runs, output, overall = await execute_definition(
            definition,
            trigger_input or {},
            parent=RunContext(db=db, organization_id=organization_id),
        )
    except Exception as exc:  # engine failure is reported, never swallowed
        node_runs, output, overall = [], {"status": "failed", "error": str(exc)}, "failed"
        execution.error_message = str(exc)
        execution.error_code = "EXECUTION_ENGINE_ERROR"

    completed_at = _now()
    failed = [run for run in node_runs if run["status"] == "failed" and not run.get("continued")]
    continued_failures = [run for run in node_runs if run["status"] == "failed" and run.get("continued")]
    status_map = {
        "completed": WorkflowExecutionStatus.COMPLETED,
        "failed": WorkflowExecutionStatus.FAILED,
        "waiting": WorkflowExecutionStatus.WAITING,
    }
    execution.status = status_map[overall]
    execution.completed_at = completed_at
    execution.metadata = {
        **(execution.metadata or {}),
        "node_runs": node_runs,
        "output": output,
        "summary": {
            "total": len(node_runs),
            "succeeded": sum(1 for run in node_runs if run["status"] == "succeeded"),
            "failed": len(failed),
            "skipped": sum(1 for run in node_runs if run["status"] == "skipped"),
            "waiting": sum(1 for run in node_runs if run["status"] == "waiting"),
            "warnings": len(continued_failures),
            "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        },
    }
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

    return {
        "execution_id": str(execution_id),
        "workflow_id": str(workflow.id),
        "status": overall,
        "message": {
            "completed": "Workflow run completed",
            "failed": "Workflow run failed",
            "waiting": "Workflow run is waiting for a human approval",
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
) -> Dict[str, Any]:
    """Execute one node (or trigger) of a definition — n8n's "Execute step".

    Expressions see the supplied trigger input as ``trigger``; nodes that did
    not run are unavailable, so ``{{nodes.<id>.*}}`` resolves empty. Retries,
    timeouts and "continue on error" apply exactly as in a full run.
    """
    trigger_input = trigger_input or {}
    triggers: List[Dict[str, Any]] = list(definition.get("triggers") or [])
    nodes: List[Dict[str, Any]] = list(definition.get("nodes") or [])

    variables: Dict[str, Any] = {}
    for variable in definition.get("variables") or []:
        if isinstance(variable, dict) and variable.get("name"):
            variables[str(variable["name"])] = variable.get("default")

    ctx = RunContext(trigger={}, variables=variables, db=db, organization_id=organization_id)
    if triggers:
        await _exec_trigger(triggers[0], trigger_input, ctx)
    else:
        ctx.trigger.update(trigger_input)

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
        outcome, attempts, continued = await _execute_with_policy(node, inputs, ctx)

    completed = _now()
    return {
        "node_id": str(item.get("id")),
        "node_type": str(item.get("type") or ""),
        "node_name": str(item.get("name") or item.get("type") or "Node"),
        "status": outcome.status,
        "inputs": inputs,
        "outputs": outcome.outputs,
        "error": outcome.error,
        "error_code": outcome.error_code,
        "reason": outcome.reason,
        "duration_ms": int((completed - started).total_seconds() * 1000),
        "attempt": attempts,
        "continued": continued,
    }
