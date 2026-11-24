"""Code-node harness: runs ONE piece of user code in a throwaway process.

This file is executed as a script by :mod:`openagent.runtime.code_runner` —
never imported by the API. It reads a JSON payload on stdin:

    {"code": "...", "language": "python", "mode": "all|each",
     "items": [{"json": {...}, "binary": {...}}], "parameters": {...},
     "max_output_bytes": 1000000}

and writes a JSON result to the path given as ``argv[1]``:

    {"items": [...]}  or  {"error": {"code": "...", "message": "..."}}

Isolation is defence in depth: the process is short-lived, has a memory/CPU
rlimit, and a ``sys.addaudithook`` guard blocks network, subprocesses, native
calls, and file reads outside the stdlib/temp sandbox. That is the development
boundary; production Code nodes use the Docker backend (``CODE_RUNNER=docker``)
where the container is the real boundary.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sys
import tempfile
import textwrap
import traceback
from typing import Any, Dict, List

_MAX_STDOUT = 64_000
_BLOCKED_IMPORTS = (
    "ctypes",
    "_ctypes",
    "subprocess",
    "multiprocessing",
    "socket",
    "ssl",
    "http",
    "urllib.request",
    "ftplib",
    "telnetlib",
    "smtplib",
    "asyncio.subprocess",
)


def _blocked(message: str) -> PermissionError:
    return PermissionError(f"{message} is disabled inside Code nodes")


def install_guard(workspace: str, result_path: str = "") -> None:
    """Fail closed on the escapes a data transform never needs."""
    root = os.path.realpath(workspace)
    result_dir = os.path.realpath(os.path.dirname(result_path)) if result_path else ""
    temp_root = os.path.realpath(tempfile.gettempdir())
    allowed_read_roots = tuple(
        root
        for root in (
            os.path.realpath(sys.prefix),
            os.path.realpath(sys.base_prefix),
            temp_root,
        )
        if root
    )

    def audit(event: str, args: Any) -> None:
        if event == "import":
            name = str(args[0] or "")
            if any(name == item or name.startswith(item + ".") for item in _BLOCKED_IMPORTS):
                raise _blocked(f"module '{name}'")
            return
        if event.startswith("socket.") or event.startswith("subprocess."):
            raise _blocked("network and subprocess access")
        if event.startswith("ctypes."):
            raise _blocked("native calls")
        if event in (
            "os.system",
            "os.exec",
            "os.spawn",
            "os.posix_spawn",
            "os.fork",
            "os.forkpty",
            "os.startfile",
        ):
            raise _blocked("process control")
        if event == "open":
            path = args[0] if args else ""
            if isinstance(path, int) or path is None:
                return
            text = str(path)
            target = os.path.realpath(text)
            mode = str(args[1] or "r")
            writing = any(flag in mode for flag in ("w", "a", "x", "+"))
            if writing:
                if target.startswith(root + os.sep) or target.startswith(temp_root + os.sep):
                    return
                if result_dir and target.startswith(result_dir + os.sep):
                    return
                if target == os.path.realpath(workspace):
                    return
                raise _blocked("file writes outside the sandbox")
            if not os.path.isabs(text):
                return
            if target.startswith(root + os.sep) or target.startswith(temp_root + os.sep):
                return
            if any(target.startswith(prefix + os.sep) for prefix in allowed_read_roots):
                return
            raise _blocked("reading this file")

    sys.addaudithook(audit)


def apply_limits(memory_mb: int, timeout_seconds: int) -> None:
    try:  # POSIX only; Windows has no rlimit.
        import resource

        memory = max(64, memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = max(1, int(timeout_seconds) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    except Exception:  # pragma: no cover - platform dependent
        pass


class _CappedWriter(io.TextIOBase):
    def __init__(self, limit: int = _MAX_STDOUT) -> None:
        self._limit = limit
        self._parts: List[str] = []
        self._size = 0
        self.truncated = False

    def write(self, text: str) -> int:  # type: ignore[override]
        if not isinstance(text, str):
            text = str(text)
        remaining = self._limit - self._size
        if remaining > 0:
            chunk = text[:remaining]
            self._parts.append(chunk)
            self._size += len(chunk)
        if len(text) > max(remaining, 0):
            self.truncated = True
        return len(text)

    def getvalue(self) -> str:
        return "".join(self._parts) + ("\n[output truncated]" if self.truncated else "")


class CodeItem:
    """One item as n8n exposes it: ``.json`` and ``.binary``."""

    __slots__ = ("json", "binary", "pairedItem")

    def __init__(self, raw: Any) -> None:
        if isinstance(raw, dict) and ("json" in raw or "binary" in raw):
            self.json = raw.get("json", {})
            self.binary = raw.get("binary", {})
            self.pairedItem = raw.get("pairedItem")
        else:
            self.json = raw
            self.binary = {}
            self.pairedItem = None

    def __getitem__(self, key: str) -> Any:
        if key == "json":
            return self.json
        if key == "binary":
            return self.binary
        raise KeyError(key)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CodeItem({self.json!r})"


class CodeInput:
    """``$input`` — n8n's Code-node input proxy."""

    __slots__ = ("_items", "_current")

    def __init__(self, items: List[CodeItem], current: CodeItem) -> None:
        self._items = items
        self._current = current

    def all(self) -> List[CodeItem]:
        return list(self._items)

    def first(self) -> CodeItem:
        return self._items[0]

    def last(self) -> CodeItem:
        return self._items[-1]

    @property
    def item(self) -> CodeItem:
        return self._current


def _helpers() -> Dict[str, Any]:
    return {
        "json_dumps": lambda value: json.dumps(value, default=str),
        "json_loads": json.loads,
    }


def _function_source(code: str) -> str:
    body = textwrap.indent(code, "    ")
    if not body.strip():
        body = "    pass\n"
    return "def __code_main__():\n" + body + "\n"


def _as_item(value: Any) -> Dict[str, Any]:
    if isinstance(value, CodeItem):
        entry: Dict[str, Any] = {"json": value.json}
        if value.binary:
            entry["binary"] = value.binary
        return entry
    if isinstance(value, dict):
        if "json" in value and set(value) <= {"json", "binary", "pairedItem"}:
            return dict(value)
        return {"json": value}
    return {"json": {"result": value}}


def normalize(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        raise ValueError(
            "Code must return an object or a list of objects (returned nothing)"
        )
    if isinstance(value, (list, tuple)):
        return [_as_item(entry) for entry in value]
    return [_as_item(value)]


def main() -> int:
    payload = json.load(sys.stdin)
    result_path = sys.argv[1]
    workspace = os.environ.get("OPENAGENT_CODE_WORKSPACE") or tempfile.mkdtemp(
        prefix="oa-code-"
    )
    os.makedirs(workspace, exist_ok=True)

    stdout = _CappedWriter()
    stderr = _CappedWriter()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, stderr  # type: ignore[assignment]

    result: Dict[str, Any] = {}
    exit_code = 0
    try:
        install_guard(workspace, result_path)
        apply_limits(
            int(payload.get("memory_mb") or 256),
            int(payload.get("timeout_seconds") or 30),
        )
        code = str(payload.get("code") or "")
        raw_items = list(payload.get("items") or [])
        items = [CodeItem(entry) for entry in raw_items]
        mode = str(payload.get("mode") or "all").lower()
        parameters = payload.get("parameters") or {}
        max_output = int(payload.get("max_output_bytes") or 1_000_000)

        compiled = compile(_function_source(code), "<code>", "exec")

        raw_item_dicts = [
            {**({"json": entry.json} if entry.json is not None else {}),
             **({"binary": entry.binary} if entry.binary else {})}
            for entry in items
        ]

        def run_for(current: CodeItem) -> List[Dict[str, Any]]:
            current_index = items.index(current) if current in items else 0
            namespace: Dict[str, Any] = {
                "__builtins__": builtins,
                # n8n Python sandbox: _items for all-items mode, _item for
                # per-item mode. `$`-prefixed names are JavaScript-only.
                "_items": raw_item_dicts,
                "_item": raw_item_dicts[current_index] if raw_item_dicts else {"json": {}},
                "items": raw_item_dicts,
                "parameters": parameters,
                "print": print,
            }
            exec(compiled, namespace)  # noqa: S102 - sandboxed harness by design
            return normalize(namespace["__code_main__"]())

        if mode == "each":
            output: List[Dict[str, Any]] = []
            for entry in items:
                output.extend(run_for(entry))
        else:
            output = run_for(items[0] if items else CodeItem({"json": {}}))

        encoded = json.dumps({"items": output}, default=str)
        if len(encoded.encode("utf-8")) > max_output:
            result = {
                "error": {
                    "code": "CODE_OUTPUT_TOO_LARGE",
                    "message": f"Code output exceeds {max_output} bytes",
                }
            }
        else:
            result = json.loads(encoded)
    except BaseException as exc:  # noqa: BLE001 - every failure is reported
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, PermissionError):
            code = "CODE_POLICY_DENIED"
        elif isinstance(exc, ValueError):
            code = "CODE_INVALID_RESULT"
        else:
            code = "CODE_EXECUTION_ERROR"
        result = {"error": {"code": code, "message": message[:2000]}}
        try:
            traceback.print_exc(file=stderr)
        except Exception:
            pass
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr

    result["stdout"] = stdout.getvalue()
    result["stderr"] = stderr.getvalue()
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, default=str)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
