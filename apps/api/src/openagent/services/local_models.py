"""Local model runtimes for the builder assistant.

Everything here talks to software that already runs on the user's machine
(Ollama, LM Studio, llama.cpp, vLLM) over HTTP. No cloud account, API key or
telemetry is involved: the API server is the only thing making requests, and
it only calls loopback (or the endpoint the user explicitly configured).

Ollama is the one runtime that supports downloading weights through an API
(``POST /api/pull``), so pulls are streamed here and exposed as pollable jobs.
Other runtimes can still be detected, listed and used for chat; their model
downloads happen in their own desktop app.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

OLLAMA_ROOT = "http://127.0.0.1:11434"
PROBE_TIMEOUT_SECONDS = 1.5
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$")


@dataclass(frozen=True)
class RuntimeCandidate:
    id: str
    name: str
    base_url: str
    api: str  # "ollama" | "openai"
    can_pull: bool
    install_url: str = ""


RUNTIME_CANDIDATES: tuple[RuntimeCandidate, ...] = (
    RuntimeCandidate(
        id="ollama",
        name="Ollama",
        base_url=OLLAMA_ROOT,
        api="ollama",
        can_pull=True,
        install_url="https://ollama.com/download",
    ),
    RuntimeCandidate(
        id="lmstudio",
        name="LM Studio",
        base_url="http://127.0.0.1:1234",
        api="openai",
        can_pull=False,
        install_url="https://lmstudio.ai",
    ),
    RuntimeCandidate(
        id="llamacpp",
        name="llama.cpp server",
        base_url="http://127.0.0.1:8080",
        api="openai",
        can_pull=False,
        install_url="https://github.com/ggml-org/llama.cpp",
    ),
    RuntimeCandidate(
        id="vllm",
        name="vLLM",
        base_url="http://127.0.0.1:8001",
        api="openai",
        can_pull=False,
        install_url="https://docs.vllm.ai",
    ),
)

# Curated small models that build workflow JSON reliably on a laptop. Sizes are
# approximate download sizes; RAM is the practical minimum for CPU inference.
RECOMMENDED_MODELS: tuple[Dict[str, Any], ...] = (
    {
        "name": "qwen2.5:0.5b-instruct",
        "label": "Qwen 2.5 0.5B",
        "size": "~400 MB",
        "size_bytes": 400_000_000,
        "min_ram_gb": 2,
        "description": "Fastest option. Great for short commands, weaker at long workflows.",
        "best_for": "quick edits, small laptops",
    },
    {
        "name": "qwen2.5:1.5b-instruct",
        "label": "Qwen 2.5 1.5B",
        "size": "~1.0 GB",
        "size_bytes": 1_000_000_000,
        "min_ram_gb": 4,
        "description": "The sweet spot: solid JSON output and still comfortable on CPU.",
        "best_for": "general workflow building",
    },
    {
        "name": "qwen2.5-coder:1.5b",
        "label": "Qwen 2.5 Coder 1.5B",
        "size": "~1.0 GB",
        "size_bytes": 1_000_000_000,
        "min_ram_gb": 4,
        "description": "Tuned for structured code and JSON; a good fit for node configs.",
        "best_for": "complex node configuration",
    },
    {
        "name": "llama3.2:1b",
        "label": "Llama 3.2 1B",
        "size": "~1.3 GB",
        "size_bytes": 1_300_000_000,
        "min_ram_gb": 4,
        "description": "Small general model with good instruction following.",
        "best_for": "chat-style requests",
    },
    {
        "name": "qwen2.5:3b-instruct",
        "label": "Qwen 2.5 3B",
        "size": "~1.9 GB",
        "size_bytes": 1_900_000_000,
        "min_ram_gb": 6,
        "description": "Better reasoning for multi-branch automations.",
        "best_for": "larger workflows",
    },
    {
        "name": "llama3.2:3b",
        "label": "Llama 3.2 3B",
        "size": "~2.0 GB",
        "size_bytes": 2_000_000_000,
        "min_ram_gb": 8,
        "description": "Balanced quality and speed if you have 8 GB of RAM.",
        "best_for": "agents and longer prompts",
    },
    {
        "name": "phi3.5:3.8b",
        "label": "Phi 3.5 3.8B",
        "size": "~2.2 GB",
        "size_bytes": 2_200_000_000,
        "min_ram_gb": 8,
        "description": "Strong small-model reasoning for its size.",
        "best_for": "diagnostics and planning",
    },
    {
        "name": "mistral:7b",
        "label": "Mistral 7B",
        "size": "~4.1 GB",
        "size_bytes": 4_100_000_000,
        "min_ram_gb": 16,
        "description": "Higher quality if you have a GPU or 16 GB of RAM.",
        "best_for": "production-quality generation",
    },
)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize_root(base_url: str) -> str:
    """Turn any endpoint form into the runtime root (no trailing ``/v1``)."""
    cleaned = (base_url or "").strip().rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    return cleaned or OLLAMA_ROOT


def normalize_openai_base(base_url: str) -> str:
    """Return an OpenAI-compatible base that always ends in ``/v1``."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return f"{OLLAMA_ROOT}/v1"
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def is_local_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} or host.endswith(".local")


def valid_model_name(name: str) -> bool:
    return bool(MODEL_NAME_RE.match((name or "").strip())) and len(name) <= 200


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _model_from_ollama(item: Dict[str, Any]) -> Dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return {
        "name": str(item.get("name") or item.get("model") or ""),
        "size_bytes": item.get("size") if isinstance(item.get("size"), int) else None,
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level"),
        "family": details.get("family"),
        "modified_at": item.get("modified_at"),
    }


def _model_from_openai(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": str(item.get("id") or ""),
        "size_bytes": None,
        "parameter_size": None,
        "quantization": None,
        "family": None,
        "modified_at": item.get("created") if isinstance(item.get("created"), int) else None,
    }


async def _probe_runtime(candidate: RuntimeCandidate) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": candidate.id,
        "name": candidate.name,
        "base_url": candidate.base_url,
        "api": candidate.api,
        "can_pull": candidate.can_pull,
        "install_url": candidate.install_url,
        "reachable": False,
        "version": None,
        "models": [],
        "error": None,
    }
    timeout = httpx.Timeout(PROBE_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if candidate.api == "ollama":
                tags = await client.get(f"{candidate.base_url}/api/tags")
                tags.raise_for_status()
                payload = tags.json()
                models = payload.get("models") if isinstance(payload, dict) else None
                result["models"] = [
                    _model_from_ollama(item)
                    for item in (models or [])
                    if isinstance(item, dict) and (item.get("name") or item.get("model"))
                ]
                try:
                    version = await client.get(f"{candidate.base_url}/api/version")
                    if version.is_success:
                        result["version"] = (version.json() or {}).get("version")
                except Exception:
                    pass
            else:
                models = await client.get(f"{candidate.base_url}/v1/models")
                models.raise_for_status()
                payload = models.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                result["models"] = [
                    _model_from_openai(item)
                    for item in (data or [])
                    if isinstance(item, dict) and item.get("id")
                ]
            result["reachable"] = True
    except Exception as exc:  # connection refused, timeout, bad JSON…
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return result


async def detect_runtimes(configured_base_url: str = "") -> List[Dict[str, Any]]:
    """Probe every known local runtime plus the configured endpoint in parallel."""
    candidates: List[RuntimeCandidate] = list(RUNTIME_CANDIDATES)
    configured_root = normalize_root(configured_base_url) if configured_base_url else ""
    known_roots = {candidate.base_url for candidate in candidates}
    if configured_root and configured_root not in known_roots:
        parsed = urlparse(configured_root)
        # Only advertise the configured endpoint as a runtime card if it is local;
        # remote endpoints are shown through the status/settings response instead.
        if is_local_url(configured_root):
            candidates.append(
                RuntimeCandidate(
                    id="configured",
                    name="Configured endpoint",
                    base_url=configured_root,
                    api="ollama" if parsed.port == 11434 else "openai",
                    can_pull=parsed.port == 11434,
                )
            )
    results = await asyncio.gather(
        *(_probe_runtime(candidate) for candidate in candidates),
        return_exceptions=False,
    )
    return list(results)


# ---------------------------------------------------------------------------
# Hardware / install hints
# ---------------------------------------------------------------------------

def _windows_memory_bytes() -> Optional[int]:
    """Total physical RAM via the Win32 API (psutil is optional)."""
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    except Exception:
        pass
    return None


def hardware_info() -> Dict[str, Any]:
    memory_bytes: Optional[int] = None
    try:
        import psutil  # type: ignore

        memory_bytes = int(psutil.virtual_memory().total)
    except Exception:
        if platform.system() == "Windows":
            memory_bytes = _windows_memory_bytes()
        else:
            try:
                with open("/proc/meminfo", encoding="utf-8") as handle:
                    for line in handle:
                        if line.startswith("MemTotal:"):
                            memory_bytes = int(line.split()[1]) * 1024
                            break
            except Exception:
                memory_bytes = None

    disk_free: Optional[int] = None
    try:
        disk_free = int(shutil.disk_usage(os.getcwd()).free)
    except Exception:
        disk_free = None

    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes,
        "memory_gb": round(memory_bytes / 1_000_000_000, 1) if memory_bytes else None,
        "disk_free_bytes": disk_free,
        "disk_free_gb": round(disk_free / 1_000_000_000, 1) if disk_free else None,
    }


def install_info() -> Dict[str, Any]:
    return {
        "ollama_installed": shutil.which("ollama") is not None,
        "ollama_download_url": "https://ollama.com/download",
        "ollama_docs_url": "https://docs.ollama.com",
        "lmstudio_download_url": "https://lmstudio.ai",
        "instructions": {
            "ollama": (
                "Install Ollama once, keep it running, then download a model here. "
                "Ollama listens on 127.0.0.1:11434 and needs no account."
            ),
            "lmstudio": (
                "Install LM Studio, download a model in its Discover tab, start the "
                "local server, then pick it here."
            ),
            "llamacpp": (
                "Start llama.cpp's server with a GGUF model on port 8080 "
                "(llama-server -m model.gguf --port 8080)."
            ),
        },
    }


def recommended_models() -> List[Dict[str, Any]]:
    return [dict(item) for item in RECOMMENDED_MODELS]


# ---------------------------------------------------------------------------
# Pull jobs (Ollama only)
# ---------------------------------------------------------------------------

@dataclass
class PullJob:
    id: str
    model: str
    base_url: str
    status: str = "running"  # running | done | error | cancelled
    progress: float = 0.0
    completed: int = 0
    total: int = 0
    detail: str = "Starting…"
    error: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "base_url": self.base_url,
            "status": self.status,
            "progress": round(self.progress, 1),
            "completed": self.completed,
            "total": self.total,
            "detail": self.detail,
            "error": self.error,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


_JOBS: Dict[str, PullJob] = {}
_JOB_ORDER: List[str] = []
MAX_JOBS = 40


def _prune_jobs() -> None:
    while len(_JOB_ORDER) > MAX_JOBS:
        oldest = _JOB_ORDER.pop(0)
        job = _JOBS.get(oldest)
        if job is not None and job.status == "running":
            # Never evict a running job; put it back at the end.
            _JOB_ORDER.append(oldest)
            return
        _JOBS.pop(oldest, None)


async def _run_pull(job: PullJob) -> None:
    try:
        timeout = httpx.Timeout(connect=10.0, read=None, write=None, pool=None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{job.base_url}/api/pull",
                json={"model": job.model, "stream": True},
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise RuntimeError(body.strip()[:300] or f"HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    status = str(event.get("status") or "")
                    completed = event.get("completed")
                    total = event.get("total")
                    if isinstance(completed, int):
                        job.completed = completed
                    if isinstance(total, int):
                        job.total = total
                    if job.total:
                        job.progress = min(99.5, job.completed / job.total * 100)
                    if status:
                        job.detail = status[:120]
                    job.updated_at = time.time()
                    if status == "success":
                        job.status = "done"
                        job.progress = 100.0
                        job.detail = "Model downloaded"
                        return
        job.status = "done"
        job.progress = 100.0
        job.detail = "Model downloaded"
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.detail = "Download cancelled"
        job.updated_at = time.time()
        raise
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"[:300]
        job.detail = "Download failed"
    finally:
        job.updated_at = time.time()


def start_pull(model: str, base_url: str = "") -> PullJob:
    """Start (or join) a background Ollama model download."""
    cleaned = (model or "").strip()
    if not valid_model_name(cleaned):
        raise ValueError("Invalid model name")
    root = normalize_root(base_url) if base_url else OLLAMA_ROOT
    for job in _JOBS.values():
        if job.status == "running" and job.model == cleaned and job.base_url == root:
            return job
    job = PullJob(id=f"pull_{uuid.uuid4().hex[:12]}", model=cleaned, base_url=root)
    job.task = asyncio.create_task(_run_pull(job))
    _JOBS[job.id] = job
    _JOB_ORDER.append(job.id)
    _prune_jobs()
    return job


def get_job(job_id: str) -> Optional[PullJob]:
    return _JOBS.get(job_id)


def cancel_job(job_id: str) -> Optional[PullJob]:
    job = _JOBS.get(job_id)
    if job is None:
        return None
    if job.status == "running" and job.task is not None:
        job.task.cancel()
        job.status = "cancelled"
        job.detail = "Download cancelled"
        job.updated_at = time.time()
    return job


async def delete_model(model: str, base_url: str = "") -> bool:
    cleaned = (model or "").strip()
    if not valid_model_name(cleaned):
        raise ValueError("Invalid model name")
    root = normalize_root(base_url) if base_url else OLLAMA_ROOT
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            "DELETE", f"{root}/api/delete", json={"model": cleaned}
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
    return True


async def test_model(model: str, base_url: str = "", api_key: str = "") -> Dict[str, Any]:
    """Send one tiny completion so the UI can prove the model actually runs."""
    cleaned = (model or "").strip()
    if not cleaned:
        raise ValueError("Model is required")
    endpoint = normalize_openai_base(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{endpoint}/chat/completions",
                headers=headers,
                json={
                    "model": cleaned,
                    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                    "max_tokens": 8,
                    "temperature": 0,
                    "stream": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        choices = payload.get("choices") or []
        content = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "reply": content.strip()[:200],
            "base_url": endpoint,
            "model": cleaned,
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "reply": "",
            "base_url": endpoint,
            "model": cleaned,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
