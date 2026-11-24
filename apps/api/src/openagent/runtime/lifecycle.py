"""Process lifecycle for workflow runs (Phase 12).

Two things need one process-wide answer and therefore live together:

* **In-flight tracking and graceful drain.** On shutdown the API stops starting
  new runs, finishes the ones already executing within a configurable timeout,
  and leaves anything queued as ``queued`` (n8n's queue-mode behaviour).
* **Instance identity.** The crash journal tags a running execution with the
  process that owns it, so a heartbeat can distinguish a slow run from a run
  whose process died.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import time
from typing import Dict, Tuple
from uuid import UUID

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 25.0


def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"
_inflight: Dict[UUID, float] = {}
_shutting_down = False


def instance_id() -> str:
    """Identity of this API process (host, pid and a per-process nonce)."""
    return _INSTANCE_ID


def shutdown_timeout_seconds() -> float:
    return env_float(
        "OPENAGENT_WORKFLOW_SHUTDOWN_TIMEOUT_SECONDS", DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    )


def begin_run(execution_id: UUID) -> None:
    _inflight[execution_id] = time.monotonic()


def end_run(execution_id: UUID) -> None:
    _inflight.pop(execution_id, None)


def active_runs() -> int:
    return len(_inflight)


def active_run_ids() -> Tuple[UUID, ...]:
    return tuple(_inflight.keys())


def begin_shutdown() -> None:
    """New runs are persisted as queued from now on; running ones may finish."""
    global _shutting_down
    _shutting_down = True


def is_shutting_down() -> bool:
    return _shutting_down


async def wait_for_drain(timeout: float | None = None) -> int:
    """Wait for in-flight runs; returns how many were still running at the end."""
    budget = shutdown_timeout_seconds() if timeout is None else max(0.0, timeout)
    deadline = time.monotonic() + budget
    while _inflight and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    return len(_inflight)


def reset_lifecycle() -> None:
    """Test hook: forget in-flight runs and the shutdown flag."""
    global _shutting_down
    _inflight.clear()
    _shutting_down = False
