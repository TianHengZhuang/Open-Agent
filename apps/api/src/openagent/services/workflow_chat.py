"""Chat payloads and stream framing for the workflow chat route.

The item shape mirrors n8n's Chat Trigger so workflows written against one
transfer to the other:

    {"json": {"chatInput": "...", "sessionId": "...", "action": "sendMessage",
              "files": [...], "metadata": {...}}}

Sessions live in the browser (localStorage), so the transcript travels with
each request as ``history`` and nothing sensitive is persisted by the API
beyond the execution record every test run already writes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

CHAT_ACTION = "sendMessage"
MAX_FILES = 6
MAX_FILE_BYTES = 4_000_000


def build_chat_item(
    message: str,
    session_id: str,
    *,
    files: Optional[Iterable[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The n8n-compatible chat item (``{json: ...}``) for a new message."""
    item: Dict[str, Any] = {
        "chatInput": str(message or ""),
        "sessionId": str(session_id or ""),
        "action": CHAT_ACTION,
    }
    kept: List[Dict[str, Any]] = []
    for raw in list(files or [])[:MAX_FILES]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:200]
        data_url = str(raw.get("data_url") or raw.get("dataUrl") or "")
        mime = str(raw.get("mime_type") or raw.get("mimeType") or "application/octet-stream")[:120]
        if not name or not data_url.startswith("data:"):
            continue
        if len(data_url) > MAX_FILE_BYTES:
            continue
        kept.append(
            {
                "name": name,
                "mimeType": mime,
                "data": data_url,
                "size": len(data_url),
            }
        )
    if kept:
        item["files"] = kept
    if metadata:
        item["metadata"] = dict(metadata)
    return {"json": item}


def clean_history(history: Optional[Iterable[Dict[str, Any]]], limit: int = 20) -> List[Dict[str, str]]:
    """Trim the transcript the client sends back (untrusted input)."""
    cleaned: List[Dict[str, str]] = []
    for entry in list(history or []):
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip().lower()
        content = entry.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        cleaned.append({"role": role, "content": text[:8000]})
    return cleaned[-limit:]


def chat_trigger_input(
    message: str,
    session_id: str,
    *,
    files: Optional[Iterable[Dict[str, Any]]] = None,
    history: Optional[Iterable[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Everything the engine needs for one chat turn."""
    item = build_chat_item(message, session_id, files=files, metadata=metadata)
    return {
        "json": item["json"],
        "history": clean_history(history),
        "message": str(message or ""),
    }


def extract_reply(node_runs: List[Dict[str, Any]]) -> tuple[str, Optional[str]]:
    """Find the assistant reply in a finished run.

    A model node's ``text`` wins (last one that produced text), then any node
    that emitted a ``text``/``reply``/``output`` string — so a Set node can be
    the reply source without an AI node.
    """
    for run in reversed(node_runs or []):
        if run.get("status") != "succeeded":
            continue
        outputs = run.get("outputs") if isinstance(run.get("outputs"), dict) else {}
        if str(run.get("node_type") or "") in {"prompt", "llm", "chat_model"}:
            text = outputs.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip(), str(run.get("node_id") or "")
    for run in reversed(node_runs or []):
        if run.get("status") != "succeeded":
            continue
        outputs = run.get("outputs") if isinstance(run.get("outputs"), dict) else {}
        for key in ("text", "reply", "output", "message"):
            value = outputs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), str(run.get("node_id") or "")
    return "", None


def summarize_runs(node_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The compact per-step trace the chat bubble shows."""
    steps: List[Dict[str, Any]] = []
    for run in node_runs or []:
        steps.append(
            {
                "node_id": str(run.get("node_id") or ""),
                "node_type": str(run.get("node_type") or ""),
                "node_name": str(run.get("node_name") or ""),
                "status": str(run.get("status") or ""),
                "duration_ms": int(run.get("duration_ms") or 0),
                "error": run.get("error"),
                "reason": run.get("reason"),
            }
        )
    return steps


def sse(event: str, data: Dict[str, Any]) -> str:
    """One server-sent event frame (JSON payload, single line)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
