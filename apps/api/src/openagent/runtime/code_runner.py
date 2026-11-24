"""Sandboxed Code-node runner (Phase 10).

A Code node never executes in the API process. The runner writes the items and
the user's code as JSON to a short-lived child process, which runs
:mod:`openagent.runtime.code_harness` under a strict guard and returns items.

Two backends:

* ``subprocess`` — default in development. A fresh interpreter/Node process
  with rlimits, no inherited environment, and an audit hook that blocks
  network, subprocesses, native calls, and file access outside the sandbox.
  Good enough for local authoring; not a hard security boundary against a
  determined CPython escape.
* ``docker`` — required in production (``OPENAGENT_ENV=production`` fails
  closed otherwise). ``docker run --rm --network none --read-only`` with a
  memory/CPU/PID cap; the image must provide the interpreter named below.

Select with ``OPENAGENT_CODE_RUNNER=auto|subprocess|docker``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

HARNESS_DIR = Path(__file__).resolve().parent
PYTHON_HARNESS = HARNESS_DIR / "code_harness.py"
JS_HARNESS = HARNESS_DIR / "code_harness.js"

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
MAX_ITEMS = 1000
MAX_CODE_CHARS = 50_000
DEFAULT_MEMORY_MB = 256

_LANGUAGES = {
    "python": "python",
    "py": "python",
    "python3": "python",
    "javascript": "javascript",
    "js": "javascript",
    "node": "javascript",
}
_ITEM_MODES = {
    "all": "all",
    "runonceforallitems": "all",
    "run_once_for_all_items": "all",
    "each": "each",
    "per_item": "each",
    "per-item": "each",
    "runonceforeachitem": "each",
    "run_once_for_each_item": "each",
}


class CodeRunnerError(Exception):
    """A Code node could not produce a result (never a fabricated success)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class CodeRunResult:
    items: List[Dict[str, Any]] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    backend: str = "subprocess"


def normalize_language(value: Any) -> Optional[str]:
    key = str(value or "").strip().lower()
    return _LANGUAGES.get(key)


def normalize_mode(value: Any) -> str:
    key = str(value or "all").strip().lower()
    return _ITEM_MODES.get(key, "all")


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 10_000_000) -> int:
    raw = os.environ.get(name)
    try:
        return max(minimum, min(int(str(raw).strip()), maximum)) if raw else default
    except (TypeError, ValueError):
        return default


def _is_production() -> bool:
    return os.environ.get("OPENAGENT_ENV", "development").lower() == "production"


def code_backend() -> str:
    raw = (os.environ.get("OPENAGENT_CODE_RUNNER") or "auto").strip().lower()
    if raw in ("docker", "subprocess"):
        return raw
    if raw not in ("", "auto"):
        raise CodeRunnerError(
            "CODE_RUNNER_INVALID",
            f"OPENAGENT_CODE_RUNNER={raw!r} is not auto, subprocess or docker",
        )
    return "docker" if shutil.which("docker") and _is_production() else "subprocess"


def _timeout_seconds(value: Any) -> int:
    try:
        requested = int(float(value)) if value is not None else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        requested = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(requested, MAX_TIMEOUT_SECONDS))


def _payload(
    *,
    language: str,
    code: str,
    items: List[Dict[str, Any]],
    mode: str,
    parameters: Dict[str, Any],
    timeout_seconds: int,
    max_output_bytes: int,
) -> Dict[str, Any]:
    return {
        "language": language,
        "code": code,
        "mode": mode,
        "items": items[:MAX_ITEMS],
        "parameters": parameters,
        "timeout_seconds": timeout_seconds,
        "max_output_bytes": max_output_bytes,
        "memory_mb": _env_int("OPENAGENT_CODE_MAX_MEMORY_MB", DEFAULT_MEMORY_MB, minimum=64),
    }


def _subprocess_argv(language: str, result_path: str) -> List[str]:
    if language == "python":
        interpreter = sys.executable or shutil.which("python3") or shutil.which("python")
        if not interpreter:
            raise CodeRunnerError(
                "CODE_RUNNER_UNAVAILABLE", "No Python interpreter is available"
            )
        return [interpreter, "-I", "-B", str(PYTHON_HARNESS), result_path]
    node = shutil.which("node")
    if not node:
        raise CodeRunnerError(
            "CODE_RUNNER_UNAVAILABLE",
            "JavaScript Code nodes need Node.js on PATH (or the Docker backend)",
        )
    return [
        node,
        "--no-warnings",
        "--disallow-code-generation-from-strings",
        str(JS_HARNESS),
        result_path,
    ]


def _docker_argv(language: str, outdir: str) -> List[str]:
    if language == "python":
        image = os.environ.get("OPENAGENT_CODE_PYTHON_IMAGE", "python:3.12-alpine")
        command = ["python", "-I", "-B", "/runner/code_harness.py", "/out/result.json"]
    else:
        image = os.environ.get("OPENAGENT_CODE_NODE_IMAGE", "node:22-alpine")
        command = [
            "node",
            "--no-warnings",
            "--disallow-code-generation-from-strings",
            "/runner/code_harness.js",
            "/out/result.json",
        ]
    memory = _env_int("OPENAGENT_CODE_MAX_MEMORY_MB", DEFAULT_MEMORY_MB, minimum=64)
    return [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--network",
        "none",
        "--memory",
        f"{memory}m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "128",
        "--read-only",
        "--tmpfs",
        "/tmp:size=16m",
        "--volume",
        f"{HARNESS_DIR}:/runner:ro",
        "--volume",
        f"{outdir}:/out",
        image,
        *command,
    ]


async def _execute(
    argv: List[str], payload: Dict[str, Any], timeout_seconds: int
) -> tuple[int, bytes, bytes, bool]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt":  # pragma: no cover - Windows needs the SystemRoot env
        env["SystemRoot"] = os.environ.get("SystemRoot", "")
        env["TEMP"] = os.environ.get("TEMP", "")
        env["TMP"] = os.environ.get("TMP", "")
    workspace = tempfile.mkdtemp(prefix="oa-code-")
    env["OPENAGENT_CODE_WORKSPACE"] = workspace
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload, default=str).encode("utf-8")),
                timeout=timeout_seconds + 2,
            )
            return process.returncode or 0, stdout, stderr, False
        except asyncio.TimeoutError:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover - very defensive
                pass
            return 124, b"", b"", True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def run_code(
    *,
    language: Any,
    code: Any,
    items: List[Dict[str, Any]],
    mode: Any = "all",
    parameters: Optional[Dict[str, Any]] = None,
    timeout_seconds: Any = None,
    max_output_bytes: Optional[int] = None,
) -> CodeRunResult:
    """Run one Code node body and return normalized ``{json}`` items."""
    normalized_language = normalize_language(language)
    if normalized_language is None:
        raise CodeRunnerError(
            "CODE_LANGUAGE_UNSUPPORTED",
            "Code node language must be python or javascript (use expression mode for {{ }})",
        )
    source = str(code or "")
    if not source.strip():
        raise CodeRunnerError("INVALID_CONFIG", "Code node needs config.code")
    if len(source) > MAX_CODE_CHARS:
        raise CodeRunnerError(
            "CODE_TOO_LARGE", f"Code exceeds {MAX_CODE_CHARS} characters"
        )

    backend = code_backend()
    if _is_production() and backend != "docker":
        raise CodeRunnerError(
            "CODE_RUNNER_INSECURE",
            "Production Code nodes require the Docker runner "
            "(OPENAGENT_CODE_RUNNER=docker); refusing host execution",
        )

    limit = max_output_bytes or _env_int(
        "OPENAGENT_CODE_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES, minimum=1024
    )
    timeout = _timeout_seconds(timeout_seconds)
    payload = _payload(
        language=normalized_language,
        code=source,
        items=items,
        mode=normalize_mode(mode),
        parameters=parameters or {},
        timeout_seconds=timeout,
        max_output_bytes=limit,
    )

    outdir = tempfile.mkdtemp(prefix="oa-code-out-")
    result_path = str(Path(outdir) / "result.json")
    started = time.monotonic()
    try:
        if backend == "docker":
            argv = _docker_argv(normalized_language, outdir)
        else:
            argv = _subprocess_argv(normalized_language, result_path)
        exit_code, stdout, stderr, timed_out = await _execute(argv, payload, timeout)
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_text = stdout.decode("utf-8", "replace")[:64_000]
        stderr_text = stderr.decode("utf-8", "replace")[:64_000]

        if timed_out:
            raise CodeRunnerError(
                "CODE_TIMEOUT", f"Code exceeded the {timeout}s limit"
            )

        result_file = Path(result_path)
        if not result_file.exists():
            detail = stderr_text.strip().splitlines()[-1] if stderr_text.strip() else ""
            if backend == "docker" and exit_code != 0:
                raise CodeRunnerError(
                    "CODE_RUNNER_UNAVAILABLE",
                    "Docker Code runner failed: " + (detail or f"exit {exit_code}"),
                )
            raise CodeRunnerError(
                "CODE_RUNNER_FAILED",
                "Code process exited without a result"
                + (f": {detail}" if detail else ""),
            )
        if result_file.stat().st_size > limit * 2:
            raise CodeRunnerError(
                "CODE_OUTPUT_TOO_LARGE", f"Code output exceeds {limit} bytes"
            )
        with result_file.open("r", encoding="utf-8") as handle:
            result = json.load(handle)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)

    error = result.get("error")
    if isinstance(error, dict):
        raise CodeRunnerError(
            str(error.get("code") or "CODE_EXECUTION_ERROR"),
            str(error.get("message") or "Code node failed")[:2000],
        )

    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        raise CodeRunnerError(
            "CODE_INVALID_RESULT", "Code must return an object or a list of objects"
        )
    normalized_items: List[Dict[str, Any]] = []
    for entry in raw_items[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            entry = {"json": entry}
        item: Dict[str, Any] = {"json": entry.get("json", {})}
        binary = entry.get("binary")
        if isinstance(binary, dict) and binary:
            item["binary"] = binary
        normalized_items.append(item)

    return CodeRunResult(
        items=normalized_items,
        stdout=str(result.get("stdout") or stdout_text)[:64_000],
        stderr=str(result.get("stderr") or stderr_text)[:64_000],
        duration_ms=duration_ms,
        backend=backend,
    )
