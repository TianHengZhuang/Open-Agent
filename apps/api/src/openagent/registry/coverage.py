"""Node coverage tiers (Phase 11).

The roadmap refuses to hand-port n8n's 308 node directories. Instead every
node has a tier and an honest status:

* **Tier A** — local deterministic nodes with a real executor. Nothing is
  skipped: a run either does the work or fails with a reason.
* **Tier B** — nodes that need a provider, a credential, a human or an
  evaluation input. Provider apps are reached through the existing connector
  registry (``connector_action``), not new transport code.
* **Tier C** — the long tail. Not built in; served by the generic HTTP
  Request node, MCP servers, or an ``.oaext`` extension import. The coverage
  answer says so instead of pretending.

``build_coverage`` is the single source for ``GET /api/v1/.../nodes/coverage``
and the chat ``node_coverage`` action. A test asserts the generated registry
and this map agree, so a new node cannot ship without a tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

STATUS_IMPLEMENTED = "implemented"
STATUS_REQUIRES_PROVIDER = "requires_provider"
STATUS_REQUIRES_CREDENTIALS = "requires_credentials"
STATUS_HUMAN = "waiting_for_human"
STATUS_REQUIRES_INPUT = "requires_input"
STATUS_CONNECTOR = "via_connector"
STATUS_USE_HTTP = "use_http_mcp"


@dataclass(frozen=True)
class TierEntry:
    type: str
    tier: str
    n8n: str
    execution: str  # local | provider | connector | human | documented
    status: str
    note: str = ""


# --- Tier A: local, deterministic, fully executed -------------------------

_TIER_A: Dict[str, tuple[str, str, str]] = {
    # type: (n8n equivalent, status, note)
    "set": ("Set", STATUS_IMPLEMENTED, ""),
    "transform": ("Set (Edit Fields)", STATUS_IMPLEMENTED, ""),
    "edit_fields": ("Set (Edit Fields)", STATUS_IMPLEMENTED, ""),
    "filter": ("Filter", STATUS_IMPLEMENTED, ""),
    "map": ("Code (map over items)", STATUS_IMPLEMENTED, ""),
    "sort": ("Sort", STATUS_IMPLEMENTED, ""),
    "limit": ("Limit", STATUS_IMPLEMENTED, ""),
    "remove_duplicates": ("Remove Duplicates", STATUS_IMPLEMENTED, ""),
    "split_out": ("Split Out", STATUS_IMPLEMENTED, ""),
    "aggregate": ("Aggregate", STATUS_IMPLEMENTED, ""),
    "split_in_batches": ("Loop Over Items (Split in Batches)", STATUS_IMPLEMENTED, ""),
    "merge": ("Merge", STATUS_IMPLEMENTED, ""),
    "condition": ("If", STATUS_IMPLEMENTED, ""),
    "switch": ("Switch", STATUS_IMPLEMENTED, ""),
    "loop": ("Loop Over Items", STATUS_IMPLEMENTED, ""),
    "variable": ("Set (workflow variables)", STATUS_IMPLEMENTED, ""),
    "delay": ("Wait", STATUS_IMPLEMENTED, "Timed wait plus webhook resume."),
    "webhook": ("HTTP Request", STATUS_IMPLEMENTED, ""),
    "respond_to_webhook": ("Respond to Webhook", STATUS_IMPLEMENTED, ""),
    "subworkflow": ("Execute Workflow", STATUS_IMPLEMENTED, ""),
    "code": ("Code", STATUS_IMPLEMENTED, "Expression locally; Python/JS in the sandbox runner."),
    "convert_to_file": ("Convert to File", STATUS_IMPLEMENTED, ""),
    "extract_from_file": ("Extract from File", STATUS_IMPLEMENTED, ""),
    "read_binary_file": ("Read/Write Files from Disk", STATUS_IMPLEMENTED, ""),
    "write_binary_file": ("Read/Write Files from Disk", STATUS_IMPLEMENTED, ""),
    "crypto": ("Crypto", STATUS_IMPLEMENTED, ""),
    "datetime": ("Date & Time", STATUS_IMPLEMENTED, ""),
    "html": ("HTML", STATUS_IMPLEMENTED, ""),
    "xml": ("XML", STATUS_IMPLEMENTED, ""),
    "compression": ("Compression", STATUS_IMPLEMENTED, ""),
    "compare_datasets": ("Compare Datasets", STATUS_IMPLEMENTED, ""),
    "no_op": ("No Operation, do nothing", STATUS_IMPLEMENTED, ""),
    "stop_and_error": ("Stop and Error", STATUS_IMPLEMENTED, ""),
}

# --- Tier B: provider / credential / human / evaluation -------------------

_TIER_B: Dict[str, tuple[str, str, str, str]] = {
    # type: (n8n equivalent, execution, status, note)
    "prompt": ("OpenAI / Anthropic chat model", "provider", STATUS_REQUIRES_PROVIDER,
               "Needs an organization model provider; skipped with a reason otherwise."),
    "agent": ("AI Agent", "provider", STATUS_REQUIRES_PROVIDER,
              "Delegates to a registered agent."),
    "tool": ("Tool nodes", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "approval": ("Wait (human approval)", "human", STATUS_HUMAN, ""),
    "connector_action": ("Slack / GitHub / Gmail / … app nodes", "connector", STATUS_CONNECTOR,
                         "Any registered connector action, no new transport code."),
    "connector_trigger": ("App trigger nodes", "connector", STATUS_CONNECTOR, ""),
    "connector_search": ("n8n node discovery", "connector", STATUS_CONNECTOR, ""),
    "connector_resource": ("App resource lookup", "connector", STATUS_CONNECTOR, ""),
    "browser_agent": ("Browserless / Puppeteer", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "browser_action": ("Browserless / Puppeteer", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "browser_extract": ("Browserless / Puppeteer", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "code_agent": ("Execute Command (coding agent)", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "code_search": ("Execute Command (workspace)", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "code_read": ("Read/Write Files from Disk (workspace)", "provider",
                  STATUS_REQUIRES_PROVIDER, ""),
    "code_patch": ("Execute Command (workspace)", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "code_test": ("Execute Command (sandbox)", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "code_lint": ("Execute Command (sandbox)", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "code_review": ("Code review (no n8n equivalent)", "provider", STATUS_REQUIRES_PROVIDER, ""),
    "git_commit": ("GitHub / GitLab", "connector", STATUS_CONNECTOR, ""),
    "create_pr": ("GitHub / GitLab", "connector", STATUS_CONNECTOR, ""),
    "verify": ("n8n Evaluation", "provider", STATUS_REQUIRES_INPUT, ""),
    "evaluate": ("n8n Evaluation", "provider", STATUS_REQUIRES_INPUT, ""),
    "assert": ("n8n Evaluation", "provider", STATUS_REQUIRES_INPUT, ""),
    "quality_gate": ("n8n Evaluation", "provider", STATUS_REQUIRES_INPUT, ""),
    "retry": ("Retry On Fail (node setting)", "provider", STATUS_REQUIRES_INPUT,
              "Node-level retry also exists on every node."),
    "correct": ("n8n Evaluation", "provider", STATUS_REQUIRES_INPUT, ""),
}

# --- Tier B connectors: an existing connection becomes a node -------------

# Every bundled connector provider is reachable as a workflow node through
# ``connector_action`` / ``connector_trigger``. The list mirrors
# ``openagent.connectors.providers.register_official`` and is cross-checked by
# a test, so a new provider cannot be silently missing from the coverage view.
CONNECTOR_NODES: Dict[str, str] = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "gmail": "Gmail",
    "slack": "Slack",
    "google_calendar": "Google Calendar",
    "google_drive": "Google Drive",
    "notion": "Notion",
    "hubspot": "HubSpot",
    "postgres": "Postgres",
    "discord": "Discord",
    "telegram": "Telegram",
}

# --- Tier C: long tail, documented as HTTP/MCP/extension ------------------

# n8n ships these as dedicated nodes. Open Agent does not hand-port them: the
# HTTP Request node, an MCP server, or an imported extension covers them.
TIER_C_ROWS: tuple[tuple[str, str], ...] = (
    ("airtable", "Airtable"),
    ("jira", "Jira"),
    ("linear", "Linear"),
    ("stripe", "Stripe"),
    ("salesforce", "Salesforce"),
    ("shopify", "Shopify"),
    ("asana", "Asana"),
    ("trello", "Trello"),
    ("monday", "Monday.com"),
    ("clickup", "ClickUp"),
    ("zendesk", "Zendesk"),
    ("intercom", "Intercom"),
    ("twilio", "Twilio"),
    ("sendgrid", "SendGrid"),
    ("mailchimp", "Mailchimp"),
    ("dropbox", "Dropbox"),
    ("box", "Box"),
    ("zoom", "Zoom"),
    ("google_sheets", "Google Sheets"),
    ("microsoft_teams", "Microsoft Teams"),
    ("openai_assistant", "OpenAI Assistant/vector store"),
    ("langchain", "LangChain agent/chain/memory nodes"),
)

TIER_C_VIA = ("webhook", "mcp", "extension_import")

TRIGGER_TIERS: Dict[str, tuple[str, str]] = {
    "manual": ("Manual Trigger", STATUS_IMPLEMENTED),
    "schedule": ("Schedule Trigger", STATUS_IMPLEMENTED),
    "webhook": ("Webhook", STATUS_IMPLEMENTED),
    "event": ("Webhook / Event", STATUS_IMPLEMENTED),
    "execute_workflow": ("Execute Workflow Trigger", STATUS_IMPLEMENTED),
    "chat": ("Chat Trigger", STATUS_IMPLEMENTED),
}


def _entry(node_type: str) -> Optional[TierEntry]:
    if node_type in _TIER_A:
        n8n, status, note = _TIER_A[node_type]
        return TierEntry(node_type, TIER_A, n8n, "local", status, note)
    if node_type in _TIER_B:
        n8n, execution, status, note = _TIER_B[node_type]
        return TierEntry(node_type, TIER_B, n8n, execution, status, note)
    return None


def mapped_node_types() -> set[str]:
    """Every node type this module has an opinion about."""
    return set(_TIER_A) | set(_TIER_B)


def unmapped_types(node_types: Iterable[str]) -> List[str]:
    """Registry types with no tier (must stay empty)."""
    return sorted(set(node_types) - mapped_node_types())


def unknown_types() -> List[str]:
    """Tier entries that no longer exist in the registry (must stay empty)."""
    try:
        from openagent.registry import node_registry

        registry = node_registry.load_registry()
    except Exception:  # noqa: BLE001 - a missing registry is reported elsewhere
        return []
    return sorted(mapped_node_types() - set(registry.latest))


def _connector_providers() -> List[str]:
    try:
        from openagent.connectors.providers import provider_ids, register_official

        if not provider_ids():
            register_official()
        return provider_ids()
    except Exception:  # noqa: BLE001 - optional at import time
        return sorted(CONNECTOR_NODES)


def connector_coverage() -> List[Dict[str, Any]]:
    """Provider apps served by the connector layer, with real provider ids."""
    available = set(_connector_providers())
    rows: List[Dict[str, Any]] = []
    for provider, n8n_name in CONNECTOR_NODES.items():
        rows.append({
            "provider": provider,
            "n8n": n8n_name,
            "tier": TIER_B,
            "status": STATUS_CONNECTOR,
            "via": ["connector_action", "connector_trigger"],
            "available": provider in available,
        })
    return rows


def documented_capabilities() -> List[Dict[str, Any]]:
    return [
        {
            "capability": capability,
            "n8n": n8n_name,
            "tier": TIER_C,
            "status": STATUS_USE_HTTP,
            "via": list(TIER_C_VIA),
            "detail": (
                "Not a built-in node. Use the HTTP Request node, an MCP server, "
                "or import an extension."
            ),
        }
        for capability, n8n_name in TIER_C_ROWS
    ]


def _keywords(definition: Any) -> str:
    """Searchable text for "can you X?" beyond the node's name."""
    if definition is None:
        return ""
    parts: List[str] = [str(definition.description or "")]
    for field in definition.config_fields:
        parts.append(str(field.label or ""))
        parts.append(str(field.help or ""))
        parts.extend(str(option.get("label") or "") for option in field.options)
    return " ".join(part for part in parts if part)[:600]


def build_coverage() -> Dict[str, Any]:
    """The full coverage document (endpoint + chat share this)."""
    from openagent.registry import node_registry

    registry = node_registry.load_registry()
    nodes: List[Dict[str, Any]] = []
    missing: List[str] = []
    for node_type in sorted(registry.latest):
        definition = registry.node(node_type)
        entry = _entry(node_type)
        if entry is None:
            missing.append(node_type)
            nodes.append({
                "type": node_type,
                "label": definition.label if definition else node_type,
                "category": definition.category if definition else "",
                "tier": TIER_C,
                "execution": "documented",
                "status": STATUS_USE_HTTP,
                "n8n": "",
                "via": list(TIER_C_VIA),
                "note": "No tier assigned; treated as use HTTP/MCP.",
            })
            continue
        nodes.append({
            "type": node_type,
            "label": definition.label if definition else node_type,
            "category": definition.category if definition else "",
            "tier": entry.tier,
            "execution": entry.execution,
            "status": entry.status,
            "n8n": entry.n8n,
            "via": [node_type],
            "note": entry.note,
            "keywords": _keywords(definition),
        })

    triggers: List[Dict[str, Any]] = []
    for trigger_type in sorted(registry.triggers):
        n8n, status = TRIGGER_TIERS.get(trigger_type, ("", STATUS_USE_HTTP))
        triggers.append({
            "type": trigger_type,
            "label": registry.triggers[trigger_type].label,
            "tier": TIER_A if status == STATUS_IMPLEMENTED else TIER_C,
            "status": status,
            "n8n": n8n,
        })

    tier_a = [row for row in nodes if row["tier"] == TIER_A]
    tier_b = [row for row in nodes if row["tier"] == TIER_B]
    connectors = connector_coverage()
    documented = documented_capabilities()

    return {
        "registry_version": registry.registry_version,
        "generated_from": registry.generated_from,
        "summary": {
            "nodes": len(nodes),
            "triggers": len(triggers),
            "tier_a": len(tier_a),
            "tier_b": len(tier_b),
            "local_implemented": sum(
                1 for row in tier_a if row["status"] == STATUS_IMPLEMENTED
            ),
            "connector_providers": sum(1 for row in connectors if row["available"]),
            "documented_use_http": len(documented),
            "unmapped": len(missing),
        },
        "tiers": {
            TIER_A: {
                "label": "Core parity — local executors",
                "description": "Runs with no provider: deterministic and test-covered.",
            },
            TIER_B: {
                "label": "Credential-backed providers",
                "description": (
                    "Reached through connector_action / connector_trigger with an "
                    "existing connection, an agent, a model provider or a human step."
                ),
            },
            TIER_C: {
                "label": "Long tail — use HTTP / MCP / extension",
                "description": (
                    "Not hand-ported. The HTTP Request node, an MCP server, or an "
                    ".oaext extension import is the supported path."
                ),
            },
        },
        "nodes": nodes,
        "triggers": triggers,
        "connectors": connectors,
        "documented": documented,
        "unmapped": missing,
    }


def coverage_summary() -> Dict[str, Any]:
    document = build_coverage()
    return {
        **document["summary"],
        "registry_version": document["registry_version"],
        "unmapped": document["unmapped"],
    }


_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "can", "could", "would",
    "does", "did", "are", "was", "have", "has", "how", "what", "which",
    "who", "there", "this", "that", "support", "supported", "built", "node",
    "nodes", "work", "workflow", "please", "able", "use", "using", "any",
}


def _needles(query: str) -> List[str]:
    words: List[str] = []
    current = ""
    for char in str(query or "").lower():
        if char.isalnum() or char == "_":
            current += char
        elif current:
            words.append(current)
            current = ""
    if current:
        words.append(current)
    return [word for word in words if len(word) > 1 and word not in _STOPWORDS]


def _score(candidate: Dict[str, Any], needles: List[str]) -> int:
    """Strong matches on names beat incidental help-text matches."""
    primary = " ".join(
        str(candidate.get(key) or "") for key in ("type", "label", "n8n", "capability")
    ).lower()
    secondary = " ".join(
        str(candidate.get(key) or "")
        for key in ("category", "note", "keywords", "detail")
    ).lower()
    score = 0
    for needle in needles:
        if needle in primary:
            score += 10
        elif needle in secondary:
            score += 1
    return score


def answer(query: str, *, limit: int = 6) -> Dict[str, Any]:
    """Honest answer to "can you do X?" for the chat control plane."""
    document = build_coverage()
    candidates: List[Dict[str, Any]] = list(document["nodes"])
    candidates.extend({
        "type": "connector_action",
        "label": row["n8n"],
        "tier": TIER_B,
        "status": row["status"],
        "n8n": row["n8n"],
        "via": row["via"],
        "note": (
            f"Connector provider '{row['provider']}' — "
            + ("available in this deployment." if row["available"]
               else "provider not registered in this deployment.")
        ),
        "keywords": row["provider"],
    } for row in document["connectors"])
    candidates.extend(document["documented"])

    needles = _needles(query)
    scored = [
        (_score(row, needles), row)
        for row in candidates
        if needles and _score(row, needles) > 0
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    # De-duplicate by (type, label) keeping the best, then cap.
    seen: set[tuple[str, str]] = set()
    matches: List[Dict[str, Any]] = []
    for _score_value, row in scored:
        key = (str(row.get("type") or row.get("capability")), str(row.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        matches.append(row)
    matches = matches[: max(1, min(limit, 20))]

    lines: List[str] = []
    for row in matches:
        name = row.get("label") or row.get("capability")
        tier = row.get("tier")
        if tier == TIER_A:
            lines.append(f"{name}: built in ({row['status']}; n8n: {row.get('n8n')}).")
        elif tier == TIER_B:
            lines.append(
                f"{name}: available through {', '.join(row.get('via') or ['connector_action'])} "
                f"({row['status']}; n8n: {row.get('n8n')}). "
                f"{row.get('note') or ''}".strip()
            )
        else:
            lines.append(
                f"{name}: not built in. Use the HTTP Request node, MCP, or an "
                f"extension import (n8n: {row.get('n8n')})."
            )

    if not lines:
        lines.append(
            "I could not match that to a node. Tier A nodes run locally; app "
            "integrations use Connector Action; everything else is HTTP Request, "
            "MCP or an extension import — I will not claim a built-in node exists."
        )

    return {
        "query": query,
        "matches": matches,
        "answer": " ".join(lines),
        "summary": document["summary"],
    }
