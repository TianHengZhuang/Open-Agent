"""Binary data for workflow items (n8n's binary data manager, local variant).

n8n stores binary payloads as a property on the item::

    {"json": {...}, "binary": {"data": {mimeType, fileName, fileSize, ...}}}

Small payloads can be inlined as base64 in ``data``; larger ones are written to
the binary store and referenced by ``storageId``. This module owns the on-disk
side: content-addressed-per-blob files under ``OPENAGENT_BINARY_DIR`` with a
retention window that the scheduler prunes alongside executions.

The module is pure I/O with no database dependency so the engine, the hook
router and tests can all use it.
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB, n8n's default binary cap
DEFAULT_INLINE_BYTES = 64 * 1024
DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600

_EXT_RE = re.compile(r"^[a-z0-9]{1,10}$")


def max_binary_bytes() -> int:
    raw = os.environ.get("OPENAGENT_BINARY_MAX_BYTES")
    try:
        value = int(str(raw).strip()) if raw else DEFAULT_MAX_BYTES
    except (TypeError, ValueError):
        value = DEFAULT_MAX_BYTES
    return max(1024, value)


def inline_threshold_bytes() -> int:
    raw = os.environ.get("OPENAGENT_BINARY_INLINE_BYTES")
    try:
        value = int(str(raw).strip()) if raw else DEFAULT_INLINE_BYTES
    except (TypeError, ValueError):
        value = DEFAULT_INLINE_BYTES
    return max(0, value)


def binary_root() -> Path:
    raw = os.environ.get("OPENAGENT_BINARY_DIR")
    return Path(raw).expanduser() if raw else Path(".data/binaries")


def retention_seconds() -> int:
    raw = os.environ.get("OPENAGENT_BINARY_RETENTION_DAYS")
    try:
        days = float(str(raw).strip()) if raw else 7.0
    except (TypeError, ValueError):
        days = 7.0
    return max(3600, int(days * 24 * 3600)) if days > 0 else DEFAULT_RETENTION_SECONDS


def _extension(file_name: str) -> str:
    suffix = Path(file_name or "").suffix.lstrip(".").lower()
    return suffix if _EXT_RE.match(suffix) else "bin"


def mime_for(file_name: str, mime_type: Optional[str] = None) -> str:
    if mime_type and "/" in mime_type:
        return mime_type
    guessed, _ = mimetypes.guess_type(file_name or "")
    return guessed or "application/octet-stream"


def _write_blob(content: bytes) -> str:
    """Persist bytes and return a storage id (file name inside the store)."""
    root = binary_root()
    root.mkdir(parents=True, exist_ok=True)
    storage_id = uuid.uuid4().hex
    target = root / storage_id
    target.write_bytes(content)
    return storage_id


def make_binary_property(
    content: bytes,
    *,
    file_name: str = "data.bin",
    mime_type: Optional[str] = None,
    force_inline: bool = False,
) -> Dict[str, Any]:
    """Build one n8n binary property for ``content``.

    Payloads up to the inline threshold (or anything when the store is
    unwritable) are base64-inlined; larger ones live on disk under a
    ``storageId``. ``data`` is omitted from a stored property so execution
    records stay small.
    """
    if not isinstance(content, (bytes, bytearray)):
        raise TypeError("binary content must be bytes")
    payload = bytes(content)
    if len(payload) > max_binary_bytes():
        raise ValueError(
            f"Binary payload exceeds {max_binary_bytes()} bytes"
        )
    resolved_name = (file_name or "data.bin").strip() or "data.bin"
    prop: Dict[str, Any] = {
        "mimeType": mime_for(resolved_name, mime_type),
        "fileName": resolved_name,
        "fileExtension": _extension(resolved_name),
        "fileSize": len(payload),
    }
    if force_inline or len(payload) <= inline_threshold_bytes():
        prop["data"] = base64.b64encode(payload).decode("ascii")
        return prop
    try:
        prop["storageId"] = _write_blob(payload)
    except OSError:
        prop["data"] = base64.b64encode(payload).decode("ascii")
    return prop


def read_binary(prop: Any) -> bytes:
    """Read a binary property back into bytes (inline or stored)."""
    if not isinstance(prop, dict):
        raise ValueError("binary property must be an object")
    inline = prop.get("data")
    if isinstance(inline, str) and inline:
        try:
            return base64.b64decode(inline, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("binary property has invalid base64 data") from exc
    storage_id = prop.get("storageId")
    if not isinstance(storage_id, str) or not storage_id:
        raise ValueError("binary property has neither data nor storageId")
    if not re.fullmatch(r"[a-f0-9]{32}", storage_id):
        raise ValueError("binary storageId is not valid")
    path = binary_root() / storage_id
    if not path.is_file():
        raise ValueError("binary payload is no longer available (pruned)")
    return path.read_bytes()


def delete_binary(prop: Any) -> bool:
    """Remove a stored payload. Inline properties are a no-op."""
    if not isinstance(prop, dict):
        return False
    storage_id = prop.get("storageId")
    if not isinstance(storage_id, str) or not re.fullmatch(r"[a-f0-9]{32}", storage_id):
        return False
    path = binary_root() / storage_id
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def prune_binaries(max_age_seconds: Optional[int] = None) -> int:
    """Delete stored payloads older than the retention window."""
    root = binary_root()
    if not root.is_dir():
        return 0
    cutoff = time.time() - (max_age_seconds or retention_seconds())
    removed = 0
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def files_root() -> Path:
    """Sandbox root for the Read/Write Binary File nodes."""
    raw = os.environ.get("OPENAGENT_FILE_DIR")
    return Path(raw).expanduser() if raw else Path(".data/files")


def resolve_user_path(value: str) -> Path:
    """Resolve a workflow-supplied path inside the file sandbox.

    Absolute paths and ``..`` escapes are rejected rather than silently
    rewritten, so a workflow cannot read or write outside its own directory.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("path is required")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("path must be relative to the workflow file directory")
    root = files_root().resolve()
    target = (root / candidate).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path escapes the workflow file directory")
    return target
