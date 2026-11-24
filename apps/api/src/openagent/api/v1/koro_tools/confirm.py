"""Chat-level confirmation tokens (Phase 13).

A destructive tool is not executed on the first request: the assistant returns
a signed token that carries the exact action it proposed. Replying
``confirm <token>`` (or resending the action with ``confirm``) applies that
action, and nothing else.

The token is stateless — an HMAC over the action plus an expiry, keyed with the
app's ``SECRET_KEY`` — so a restart never loses a pending confirmation and no
table is needed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from openagent.core.config import get_settings

PREFIX = "cfm1"
DEFAULT_TTL_SECONDS = 300
_DEV_SECRET = "openagent-development-confirmation-key"


def _secret() -> bytes:
    key = get_settings().SECRET_KEY or _DEV_SECRET
    return key.encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _signature(body: bytes) -> str:
    return hmac.new(_secret(), body, hashlib.sha256).hexdigest()[:40]


def issue_confirmation(
    action: Dict[str, Any],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> str:
    """Sign one proposed action into a single-use confirmation token."""
    moment = time.time() if now is None else now
    payload = {key: value for key, value in action.items() if key != "confirm"}
    body = json.dumps(
        {"action": payload, "expires": int(moment) + max(1, int(ttl_seconds))},
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return f"{PREFIX}.{_b64encode(body)}.{_signature(body)}"


def decode_confirmation(
    token: str, *, now: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Return the signed action, or None when the token is invalid/expired."""
    if not token or not isinstance(token, str):
        return None
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    try:
        body = _b64decode(parts[1])
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_signature(body), parts[2]):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    moment = time.time() if now is None else now
    if not isinstance(data, dict) or int(data.get("expires") or 0) < int(moment):
        return None
    action = data.get("action")
    return action if isinstance(action, dict) else None


def confirmation_reply(action_type: str) -> str:
    return (
        f"“{action_type}” changes saved data. Reply “confirm <token>” "
        "with the token below to apply it."
    )


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "confirmation_reply",
    "decode_confirmation",
    "issue_confirmation",
]
