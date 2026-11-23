"""Local-first chat completions for workflow ``prompt`` nodes and the chat route.

Everything here speaks the OpenAI-compatible ``/v1/chat/completions`` shape,
which Ollama, LM Studio, llama.cpp's server, LocalAI and vLLM all implement.
That keeps a single client for "run this on my machine" and "use a hosted
provider", and it means no workflow chat depends on a cloud service.

The endpoint comes from the organization's copilot settings (the same ones the
Koro panel writes), falling back to the environment defaults. When a prompt
node names its own model we keep the configured endpoint and swap the model id,
so a workflow can mix a small router model with a larger reply model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.connectors.crypto import decrypt_secret
from openagent.core.config import get_settings
from openagent.db.models import Organization

MAX_MESSAGE_CHARS = 32_000
MAX_HISTORY_MESSAGES = 40
STREAM_READ_TIMEOUT_SECONDS = 300.0

_PROVIDERS = (
    ("11434", "Ollama"),
    ("1234", "LM Studio"),
    ("8080", "llama.cpp"),
    ("8001", "vLLM"),
    ("api.openai.com", "OpenAI"),
    ("openrouter.ai", "OpenRouter"),
    ("api.groq.com", "Groq"),
    ("api.mistral.ai", "Mistral"),
    ("api.together.xyz", "Together"),
    ("api.deepseek.com", "DeepSeek"),
)


class ModelRuntimeError(Exception):
    """A model endpoint refused or broke mid-call (reported, never faked)."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ModelTarget:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 60.0
    provider: str = "OpenAI-compatible"
    local: bool = True


def describe_endpoint(base_url: str) -> tuple[str, bool]:
    """Return (provider label, is_local) for an endpoint URL."""
    parsed = urlparse(base_url or "")
    host = (parsed.hostname or "").lower()
    port = str(parsed.port or "")
    local = host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} or host.endswith(".local")
    for needle, label in _PROVIDERS:
        if needle == port and local and label in {"Ollama", "LM Studio", "llama.cpp", "vLLM"}:
            return label, local
        if needle in host and needle not in {"11434", "1234", "8080", "8001"}:
            return label, local
    return "OpenAI-compatible" if not local else "Local endpoint", local


def normalize_chat_base(base_url: str) -> str:
    """Return a base that ``/chat/completions`` can be appended to."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return "http://127.0.0.1:11434/v1"
    if cleaned.endswith("/chat/completions"):
        return cleaned[: -len("/chat/completions")]
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


async def resolve_model_target(
    db: AsyncSession,
    organization_id: UUID,
    *,
    requested_model: str = "",
) -> Optional[ModelTarget]:
    """Resolve the organization's chat endpoint (settings over env defaults)."""
    settings = get_settings()
    organization = (
        await db.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    block: Dict[str, Any] = {}
    if organization is not None and isinstance(organization.settings, dict):
        candidate = organization.settings.get("copilot")
        if isinstance(candidate, dict):
            block = candidate

    base_url = str(block.get("base_url") or settings.OPENAGENT_KORO_BASE_URL or "")
    model = str(requested_model or block.get("model") or settings.OPENAGENT_KORO_MODEL or "")
    api_key = settings.OPENAGENT_KORO_API_KEY or ""
    envelope = block.get("api_key_encrypted")
    if envelope and organization is not None:
        try:
            api_key = decrypt_secret(
                settings.ENCRYPTION_KEY,
                str(envelope),
                associated=f"copilot:{organization.id}",
            )
        except Exception:
            # A rotated key must surface as a clear call error, not a crash.
            api_key = ""
    if not base_url.strip() or not model.strip():
        return None
    provider, local = describe_endpoint(base_url)
    return ModelTarget(
        base_url=normalize_chat_base(base_url),
        model=model.strip()[:200],
        api_key=api_key,
        timeout=float(settings.OPENAGENT_KORO_TIMEOUT_SECONDS or 60.0),
        provider=provider,
        local=local,
    )


def with_model(target: ModelTarget, model: str) -> ModelTarget:
    cleaned = (model or "").strip()[:200]
    return replace(target, model=cleaned) if cleaned else target


def clean_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Keep only well-formed chat turns; trim pathological payloads."""
    cleaned: List[Dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        cleaned.append({"role": role, "content": text[:MAX_MESSAGE_CHARS]})
    return cleaned[-MAX_HISTORY_MESSAGES:]


def _headers(target: ModelTarget) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    return headers


def _payload(
    target: ModelTarget,
    messages: List[Dict[str, str]],
    *,
    stream: bool,
    temperature: float,
    max_tokens: Optional[int],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": target.model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    return payload


def _error_text(response_text: str, status_code: int) -> str:
    text = (response_text or "").strip()
    try:
        parsed = json.loads(text)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:400]
        if isinstance(error, str) and error:
            return error[:400]
        if isinstance(parsed, dict) and parsed.get("message"):
            return str(parsed["message"])[:400]
    except (TypeError, ValueError):
        pass
    return (text or f"HTTP {status_code}")[:400]


async def chat_stream(
    target: ModelTarget,
    messages: List[Dict[str, Any]],
    *,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    """Call the model and stream deltas (or return the whole reply at once)."""
    cleaned = clean_messages(messages)
    if not cleaned:
        raise ModelRuntimeError("No chat messages to send to the model")
    streaming = on_delta is not None
    url = f"{target.base_url}/chat/completions"
    timeout = httpx.Timeout(connect=10.0, read=STREAM_READ_TIMEOUT_SECONDS, write=30.0, pool=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            if not streaming:
                response = await client.post(
                    url,
                    headers=_headers(target),
                    json=_payload(
                        target, cleaned, stream=False, temperature=temperature, max_tokens=max_tokens
                    ),
                )
                body = response.text
                if response.status_code >= 400:
                    raise ModelRuntimeError(
                        _error_text(body, response.status_code), status_code=response.status_code
                    )
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    raise ModelRuntimeError("The model endpoint returned no choices")
                return str((choices[0].get("message") or {}).get("content") or "")

            chunks: List[str] = []
            async with client.stream(
                "POST",
                url,
                headers=_headers(target),
                json=_payload(
                    target, cleaned, stream=True, temperature=temperature, max_tokens=max_tokens
                ),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ModelRuntimeError(
                        _error_text(body, response.status_code), status_code=response.status_code
                    )
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        chunks.append(content)
                        if on_delta is not None:
                            await on_delta(content)
            reply = "".join(chunks)
            if not reply:
                raise ModelRuntimeError("The model returned an empty reply")
            return reply
    except ModelRuntimeError:
        raise
    except httpx.HTTPError as exc:
        raise ModelRuntimeError(f"{type(exc).__name__}: {exc}"[:400]) from exc
    except ValueError as exc:
        raise ModelRuntimeError(f"The model endpoint returned invalid JSON: {exc}"[:400]) from exc
