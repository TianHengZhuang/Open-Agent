"""Prometheus text rendering for workflow operations (Phase 12).

The JSON endpoint (``/api/v1/operations/workflows``) stays the human/API view;
this renders the same snapshot in the Prometheus text exposition format so a
scrape can be compared with it value for value.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(pairs: Dict[str, Any]) -> str:
    if not pairs:
        return ""
    body = ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(pairs.items()))
    return "{" + body + "}"


def _number(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    return "0"


def render_workflow_metrics(snapshot: Dict[str, Any]) -> str:
    """Render ``execution_metrics`` output as Prometheus exposition text."""
    lines: List[str] = []
    totals = snapshot.get("totals") or {}
    by_status = totals.get("by_status") or {}
    queue = snapshot.get("queue") or {}
    runs = snapshot.get("runs") or {}

    lines.append("# HELP workflow_executions Workflow executions by current status.")
    lines.append("# TYPE workflow_executions gauge")
    for status, count in sorted(by_status.items()):
        lines.append(f"workflow_executions{_labels({'status': status})} {_number(count)}")
    lines.append(f"workflow_executions{_labels({'status': 'all'})} {_number(totals.get('all'))}")

    lines.append("# HELP workflow_queue_depth Executions waiting for a slot or worker.")
    lines.append("# TYPE workflow_queue_depth gauge")
    lines.append(f"workflow_queue_depth {_number(queue.get('depth'))}")

    lines.append("# HELP workflow_queue_oldest_seconds Age of the oldest queued execution.")
    lines.append("# TYPE workflow_queue_oldest_seconds gauge")
    lines.append(
        f"workflow_queue_oldest_seconds {_number(queue.get('oldest_queued_seconds'))}"
    )

    lines.append("# HELP workflow_running_executions Executions currently executing.")
    lines.append("# TYPE workflow_running_executions gauge")
    lines.append(f"workflow_running_executions {_number(totals.get('running'))}")

    lines.append("# HELP workflow_failure_rate Failed runs divided by finished runs in the window.")
    lines.append("# TYPE workflow_failure_rate gauge")
    lines.append(f"workflow_failure_rate {_number(runs.get('failure_rate'))}")

    lines.append("# HELP workflow_runs_finished Finished runs in the window.")
    lines.append("# TYPE workflow_runs_finished gauge")
    finished = int(runs.get("completed") or 0) + int(runs.get("failed") or 0)
    lines.append(f"workflow_runs_finished {_number(finished)}")

    count = finished
    p50 = runs.get("p50_ms")
    p95 = runs.get("p95_ms")
    avg = runs.get("avg_ms")
    lines.append("# HELP workflow_run_duration_ms Workflow run duration in the window.")
    lines.append("# TYPE workflow_run_duration_ms summary")
    if p50 is not None:
        lines.append(
            f"workflow_run_duration_ms{_labels({'quantile': '0.5'})} {_number(p50)}"
        )
    if p95 is not None:
        lines.append(
            f"workflow_run_duration_ms{_labels({'quantile': '0.95'})} {_number(p95)}"
        )
    total_ms = float(avg) * count if avg is not None and count else 0.0
    lines.append(f"workflow_run_duration_ms_sum {_number(total_ms)}")
    lines.append(f"workflow_run_duration_ms_count {count}")
    return "\n".join(lines) + "\n"


def render_extra(texts: Iterable[str]) -> str:
    """Join additional exposition fragments, dropping empty ones."""
    body = "\n".join(text.rstrip("\n") for text in texts if text and text.strip())
    return body + "\n" if body else ""
