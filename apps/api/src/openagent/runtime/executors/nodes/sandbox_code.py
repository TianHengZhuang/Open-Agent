"""Sandboxed Code node executor (Phase 10).

``language: python | javascript`` hands the node to
:mod:`openagent.runtime.code_runner` (a throwaway guarded process, or Docker in
production). Without ``language`` — or with ``language: expression`` — the
engine keeps its original expression behaviour so stored workflows run
unchanged.

Supported n8n surface: ``$input.all()/first()/last()/.item``, ``$json``,
``$helpers``, ``items``, ``parameters``; modes ``runOnceForAllItems`` and
``runOnceForEachItem``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from openagent.runtime.code_runner import (
    CodeRunnerError,
    normalize_language,
    normalize_mode,
    run_code,
)
from openagent.runtime.items import as_items, item_json, make_item


def _outcome(status: str, **kwargs: Any):
    from openagent.runtime.local_engine import NodeOutcome

    return NodeOutcome(status=status, **kwargs)


def is_sandboxed_code(node: Dict[str, Any]) -> bool:
    config = node.get("config") or {}
    return normalize_language(config.get("language")) is not None


def _source_items(inputs: Dict[str, Any], ctx: Any) -> List[Dict[str, Any]]:
    if ctx.input_items:
        return list(ctx.input_items)
    collected: List[Any] = []
    for value in inputs.values():
        collected.extend(as_items(value))
    return collected


def _timeout(node: Dict[str, Any]) -> Any:
    config = node.get("config") or {}
    for candidate in (node.get("timeout_seconds"), config.get("timeout_seconds")):
        if candidate is not None:
            return candidate
    return None


async def execute_code_node(node: Dict[str, Any], inputs: Dict[str, Any], ctx: Any):
    """Run a real (sandboxed) Code node body."""
    from openagent.runtime.local_engine import MAX_LOOP_ITEMS

    config = node.get("config") or {}
    language = normalize_language(config.get("language"))
    if language is None:
        return _outcome(
            status="failed",
            error=(
                "Code node language must be python or javascript "
                "(leave it empty for expression mode)"
            ),
            error_code="CODE_LANGUAGE_UNSUPPORTED",
        )

    raw_code = config.get("code")
    if raw_code is None or not str(raw_code).strip():
        return _outcome(
            status="failed",
            error="Code node needs config.code",
            error_code="INVALID_CONFIG",
        )

    items = _source_items(inputs, ctx)[:MAX_LOOP_ITEMS]
    parameters = {
        "workflow_id": str(getattr(getattr(ctx, "workflow", None), "id", "") or ""),
        "execution_id": str(getattr(ctx, "execution_id", "") or ""),
        "trigger": ctx.trigger.get("input", {}) if isinstance(ctx.trigger, dict) else {},
    }

    try:
        result = await run_code(
            language=language,
            code=raw_code,
            items=items,
            mode=config.get("mode") or node.get("run_mode") or "all",
            parameters=parameters,
            timeout_seconds=_timeout(node),
        )
    except CodeRunnerError as exc:
        return _outcome(
            status="failed",
            error=exc.message,
            error_code=exc.code,
            outputs={"language": language, "backend": "sandbox"},
        )
    except Exception as exc:  # a runner bug must not kill the whole run
        return _outcome(
            status="failed",
            error=str(exc)[:1000],
            error_code="CODE_RUNNER_FAILED",
            outputs={"language": language, "backend": "sandbox"},
        )

    output_items: List[Dict[str, Any]] = []
    for entry in result.items:
        item = make_item(entry.get("json", {}))
        binary = entry.get("binary")
        if isinstance(binary, dict) and binary:
            item["binary"] = binary
        output_items.append(item)

    values = [item_json(entry) for entry in output_items]
    outputs = {
        "items": values,
        "count": len(values),
        "language": language,
        "mode": normalize_mode(config.get("mode") or node.get("run_mode") or "all"),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if len(values) == 1 and isinstance(values[0], dict):
        # Keep common `{{ nodes.code.field }}` reads working.
        outputs.update(
            {key: value for key, value in values[0].items() if key not in outputs}
        )
    return _outcome(status="succeeded", outputs=outputs, items=output_items)
