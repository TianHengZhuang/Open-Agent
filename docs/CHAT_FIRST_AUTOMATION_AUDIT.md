# Chat-First Automation Audit

**Repository:** `D:\open agent` (Open-Agent, `@openagent/web`, FastAPI API)
**Date:** 2026-11-22
**Scope:** n8n-class, chat-first automation upgrade. This document is audit-only; no production behaviour was changed.

---

## 1. Method

- Read the monorepo layout, `apps/web` App Router pages/components, `apps/api` routers/services/runtime, the SQLAlchemy models and Alembic history, the workflow validator, the local execution engine, the schedule runner, the webhook handler, the Koro copilot, the node catalog and the test suites.
- Compared against n8n's public documentation: item-based data model, node-stack execution, lifecycle hooks, expression sandbox, queue mode, error workflows, versioning.
- Every status below cites the file(s) it was derived from.

---

## 2. Stack and topology

| Layer | Reality |
|---|---|
| Frontend | Next.js App Router, React, Tailwind; coral/warm design tokens in `apps/web/src/app/globals.css`; `oa-*` / `oa-studio-*` classes |
| Backend | FastAPI + SQLAlchemy async, Alembic (**27 migrations** in `apps/api/alembic/versions`) |
| DB | PostgreSQL in normal deployments; SQLite supported for local runs |
| Queue | **No Redis-backed job queue for workflow executions.** Executions run in-process through `runtime/local_engine.run_workflow`; `cloud/queues.py` is for cloud sandboxes, and `package.json` has a `dev:worker` script for the cloud worker path — not the workflow engine |
| AI | `api/v1/koro.py` (chat + JSON action protocol), model router in `runtime/` and `services/model_runtime.py`, local model support in `services/local_models.py` |
| Tests | Web: 40 files / 250 tests (Vitest). API: **79 test files** (pytest) |

---

## 3. Existing implementation (by area)

### 3.1 Frontend

- **Chat hub** `components/assistant/AssistantWorkspace.tsx`: multi-thread history, pin/rename/delete/search, resources, streaming replies, attachments, execution summaries.
- **Composer** `components/assistant/AssistantComposer.tsx`: `+` menu, Build/Plan mode, attachments, `@` mentions (`features/assistant/resources.ts`), dictation (`useSpeechInput.ts`), model pill.
- **Model picker** `components/assistant/ModelPicker.tsx` + `lib/ai-models.ts`: local (Ollama, LM Studio, LocalAI, custom) and BYOK providers, discovery, health, favourites/recents.
- **Studio** `app/workflows/[id]/edit/page.tsx`: canvas, NDV (`NodeDetailView.tsx`), inspector, run panel, execution log, Koro drawer, n8n export.
- **Canvas** `components/workflows/WorkflowCanvas.tsx`: dot grid, drag/connect, box select, copy/paste/duplicate, `D` disable, sticky notes, minimap, zoom/pan.
- **NDV**: INPUT / PARAMETERS / OUTPUT panes, pinned data (`node-data.ts`), input source chips.
- **Sidebar** recently reduced to Chat / Workflows / Executions / Settings with a `More` disclosure.
- **Workspace chat route** `app/chat/[workflowId]/page.tsx` for hosted Chat Trigger workflows.

### 3.2 Backend

- **Workflow CRUD/versioning**: `api/v1/workflows.py`, models `workflows`, `workflow_versions`, `workflow_executions`; publish/draft status on the version row; executions reference `workflow_version_id`.
- **Definition contract**: `services/workflow_definition.py` — schema version `1.0`, trigger/node/edge references validated, duplicate route names rejected, **cycle detection**, expression reference checks (advisory), credential-only keys enforced for sensitive fields.
- **Runtime**: `runtime/local_engine.py` — topological execution with `RunContext`, per-node `NodeOutcome` (`succeeded/failed/skipped/waiting`), retry policy, `on_error: continue`, sub-workflow execution, approval/wait status, node types: prompt/llm, set, transform, filter/map, sort, limit, remove_duplicates, split_out, condition, switch, merge, loop, variable, delay, webhook (HTTP), approval, subworkflow.
- **Scheduler**: `runtime/schedule_runner.py` — in-process 20 s tick, five-field cron with lists/ranges/steps/names, per-minute dedupe, disable flag.
- **Webhooks**: `api/v1/workflow_hooks.py` — `POST /api/v1/hooks/<path>`, only **active** versions listen, optional literal shared secret compared with `hmac.compare_digest` (credential refs/expressions skipped), JSON body → trigger input.
- **Approvals**: `approval_*` tables, `api/v1/approvals.py`, delegation/escalation/policies.
- **Credentials**: `connectors/crypto.py` (`encrypt_secret`), `api/v1/integrations.py` stores encrypted payloads, masked on read.
- **Security**: `connectors/netsec.py` (SSRF-style network guards), structured log redaction (`core/logging.py`), RBAC helpers in `services/authorization.py` + `lib/permissions.ts`.
- **Koro**: `api/v1/koro.py` returns `{reply, action}` with action types `run | publish | unpublish | list | inspect | explain | diagnose | create | build | edit | create_agent | none`, plus `_chat_core`, SSE `/chat/stream`, model/base-url override guarded server-side.
- **Execution observability**: `workflow_executions`, `execution_events`, `RunPanel`/`ExecutionLog` UI.

### 3.3 Node ecosystem

- Frontend catalog: `features/workflows/node-catalog.ts` (**42 node definitions + 5 triggers**) with config fields, defaults, credentials, sensitivity; serialized into the shared registry.
- Backend engine implements the data/logic subset above; many catalog entries are agent/code/orchestration nodes handled by other services.
- Integrations live under `connectors/` (Gmail, Slack, GitHub, DB, browser, …) with credential schemas.
- n8n JSON interchange: `features/workflows/n8n-format.ts` (common-node subset, warnings for unmapped nodes).

---

## 4. Missing features

| Area | Missing |
|---|---|
| Data model | `INodeExecutionData` wrapper (`json`, `binary`, `pairedItem`), per-item looping with `executeOnce`, item lineage in engine + UI |
| Expressions | Full `$input/$node/$now/$execution` evaluator in the **backend**; live preview in NDV; type validation; safe helper library. A client preview evaluator now exists (`lib/engine/expressions.ts`) but is not the execution path |
| Execution | Durable checkpoints/resume, pause/cancel API for arbitrary nodes, workflow-level timeout, parallel branch execution policy |
| Reliability | Error workflows, dead-letter queue, idempotency keys, duplicate-event guard for webhooks |
| Queue | Redis/RQ (or equivalent) workflow job queue, worker pool, concurrency/priority, worker health, graceful shutdown, distributed locking |
| Triggers | Polling triggers, signed webhook verification (HMAC), test-vs-production webhook URLs, trigger lifecycle UI |
| Nodes | IF multi-rule AND/OR UI, Switch fallback output, Merge modes (by key/position), Aggregate, Compare Datasets, Date & Time, Crypto, HTML/XML/Markdown extract, Python code sandbox, vector store/embeddings/document loaders, AI Agent sub-node ports |
| Binary | Binary data handling end-to-end (attachments are text-only today) |
| NDV | Fixed/Expression toggle with evaluation, output schema tree, "always output data", per-node settings surface for retry/onError |
| Chat control | Commands for delete/duplicate/rename/export/import/credentials/schedules/versions; destructive-action confirmation; context pinning |
| Voice | Text-to-speech, multilingual commands, confirmation-by-voice |
| Collaboration | Multi-user editing, comments, presence |
| Import/export | Full n8n type/version mapping; credential references; `pinData` round-trip is partial |

---

## 5. Incomplete / fragile areas

1. **Koro action surface is narrow** — no delete, duplicate, credential, schedule, version or execution-control actions; the model returns a single action per turn.
2. **Expression support is split** — validation (web), preview (new web engine), backend `resolve` with a different feature set. Divergence will produce "works in preview, fails in run" bugs.
3. **`Merge` is authoring-hint only** — engine merges inputs as append; catalog offers modes the engine does not implement.
4. **Schedule loop is per-process** — documented race window with multiple API processes; no leader election.
5. **Webhook security** — literal shared secret only; no HMAC signature, timestamp/replay protection, or per-trigger test URL.
6. **Attachments** — `features/assistant/attachments.ts` accepts text formats only; PDF/images are rejected (disclosed to users).
7. **n8n import** — unknown nodes degrade to `transform` with preserved params; no `typeVersion` migrations, no sub-node graph.
8. **NDV** — no parameter-level expression toggle; pinned data is UI-only by design (does not affect runs, unlike n8n).
9. **No error workflow / no workflow-level timeout**.
10. **Version activation** — publish state exists on versions, but webhook "test vs production" separation and active-execution pinning semantics need verification under queue mode.

---

## 6. Architecture limitations

- **Single-process execution**: long AI/HTTP nodes block the API event loop's worker; no isolation, no concurrency budget, no backpressure.
- **Engine and definition coupling**: `local_engine.py` both validates runtime shape and persists execution rows; n8n's separation (engine ↔ lifecycle hooks) is absent, making queue/worker adoption harder.
- **No canonical item contract**: nodes pass ad-hoc dicts/lists, so per-item error attribution and lineage are impossible.
- **Chat tool protocol is JSON-in-text**, not schema-constrained function calling; local models without JSON discipline cause repair loops (`_repair_action`).
- **Two runtimes for expressions** (TS preview, Python execute) with no shared test vectors.
- **Frontend catalog and backend `NODE_TYPES` are duplicated lists** — drift risk (44 authoring definitions vs the engine's handled subset).
- **No plugin boundary** for custom nodes; extensions exist for developer packages but not for engine-level nodes.

---

## 7. Security risks

| Risk | Severity | Notes / mitigation today |
|---|---|---|
| Prompt-driven destructive actions | High | Koro has run/publish; no confirmation UX for delete (not implemented yet). Permission checks exist server-side per action |
| SSRF via HTTP/Webhook nodes | High | `connectors/netsec.py` guards connector traffic; verify the workflow `webhook` node path uses it (currently `httpx` call inside engine — must be routed through netsec) |
| Secret leakage into prompts/logs | Medium | Log redaction + masked credentials; expression context must never expose decrypted secrets |
| Unrestricted expression evaluation | High (backend) | Backend `resolve` is limited, but any future `eval` must be sandboxed; the new web evaluator shadows globals only (not a security boundary) |
| Webhook replay/spoofing | Medium | Shared-secret only, no HMAC/timestamp |
| Cross-org access via chat actions | Medium | Server re-authorises action targets; needs tests for every new action |
| Credential exfiltration through AI tool calls | High | Keep key handling server-side; hosted-provider override already rejected (`KORO_OVERRIDE_NOT_LOCAL`) |
| Resource exhaustion from generated loops | Medium | `MAX_LOOP_ITEMS`, per-node caps exist; no workflow-level budget |

---

## 8. Migration requirements

1. **Execution data**: add `items` JSON alongside existing node output fields (dual-read, single-write) before switching the engine; keep old executions readable.
2. **Definition schema 1.1**: add `error_workflow_id`, `timezone`, `execution_timeout`, `active` (already on workflow), `settings.sticky_notes` retained; no node-config changes required.
3. **New tables**: `workflow_waits` (durable wait/resume), `webhook_deliveries` (idempotency/replay), `error_workflow_runs` (or reuse `workflow_executions.metadata`), queue tables if Redis is adopted.
4. **Alembic**: new revisions must be additive with server defaults; no destructive column drops; SQLite compatibility (`batch_alter_table`).
5. **n8n import**: no DB migration; mapping expands in `n8n-format.ts` + tests.
6. **Chat commands**: action audit rows should reuse `audit_logs`; no schema change.

---

## 9. Implementation priorities

| Priority | Work | Why |
|---|---|---|
| P0 | Canonical item model + per-item execution (`items.py`, engine + `node-data.ts`) | Everything else (lineage, retries, debugging, binary) builds on it |
| P0 | One expression engine with shared test vectors (Python authoritative, TS preview generated from the same grammar) | Removes preview/run divergence; required by chat parameter generation |
| P0 | Koro action registry + schema-validated tool calls with confirmation for destructive actions | Chat-first product requirement; prevents unsafe edits |
| P1 | Durable wait/resume + cancellation + workflow timeout | Makes approvals/wait usable in production |
| P1 | Error workflows + idempotency + HMAC webhooks | Production reliability and security |
| P1 | Execution observability in chat (timeline, slowest node, failure explanation) | Chat-first operations |
| P2 | Redis queue + workers behind a flag; keep SQLite/in-process default | Scale without breaking local dev |
| P2 | Node parity batches (IF multi-rule, Merge modes, Aggregate, Date/Crypto/Extract) | n8n-class breadth |
| P2 | NDV expression toggle + settings surface, binary data | Studio completeness |
| P3 | Voice TTS/multilingual, collaboration, custom node SDK, full n8n import | Differentiators / ecosystem |

---

## 10. Baseline evidence

- `pnpm --filter @openagent/web typecheck` → 0 errors (this session).
- `pnpm --filter @openagent/web test` → 250 passed / 40 files.
- `pnpm --filter @openagent/web build` → compiled successfully.
- API test suite exists (79 files) but was not run in this audit phase.
