# Chat-First Automation Architecture

**Goal:** one conversational control plane over the existing Open-Agent workflow engine — no second engine, no parallel data store, no chat-only mock layer.

---

## 1. Principles

1. **One engine.** `runtime/local_engine.py` remains the only workflow executor; it is refactored into engine core + lifecycle hooks, not duplicated.
2. **One definition.** `services/workflow_definition.py` + `features/workflows/types.ts` stay the contract; schema changes are additive and versioned.
3. **One action surface.** Every chat operation is a typed action executed by a backend service that already owns the permission check.
4. **Chat is a client, not a bypass.** Koro actions call the same services/routes the UI calls; the UI and chat share validation, permissions and audit.
5. **No fake success.** An action reports success only after the database write/execution record exists.

---

## 2. Service boundaries (reuse map)

| Service | Responsibility | Existing code to reuse | New code |
|---|---|---|---|
| Conversation Engine | Threads, messages, streaming, attachments | `components/assistant/*`, `api/v1/koro.py` (`/chat`, `/chat/stream`), thread storage | Persist thread ↔ workflow linkage; pagination |
| Intent Parser | Classify message → action candidates | Koro JSON action protocol, `_repair_action` | Schema-constrained action router (`ActionRegistry`) |
| Workflow Planner | Message → ordered mutation plan | `features/workflows/mutations.ts`, backend definition builder | Planner service that emits the shared plan schema |
| Workflow Compiler | Plan → validated definition + human preview | definition validator, `n8n-format`, layout | Compiler wrapper with diff preview + credential check |
| Node Registry | Node metadata/versions/schemas for UI, engine, AI | `node-catalog.ts`, backend `NODE_TYPES` | Single source export (`registry.json` generated at build) |
| Expression Runtime | Parse/evaluate `{{ }}` with one grammar | web validator, backend `resolve`, new `lib/engine/expressions.ts` | Python evaluator + shared test vectors; NDV preview |
| Execution Runtime | Run graphs, checkpoints, cancel, timeout | `runtime/local_engine.py`, `schedule_runner.py` | Engine core extracted behind lifecycle hooks; `runtime/items.py` |
| Trigger Manager | Manual/webhook/cron/event/chat activation | `workflow_hooks.py`, `schedule_runner.py`, chat trigger route | Unified activation service + trigger status API; HMAC + deliveries |
| Credential Vault | Encrypt, mask, resolve at use | `connectors/crypto.py`, `api/v1/integrations.py` | `resolve_for_execution(org, id)` with audit; rotation API |
| Execution Event System | Timeline, SSE, audit | `execution_events`, `ExecutionLog`, `/chat/stream` | Event bus interface (`runtime/events.py`) + chat summary cards |
| AI Debugger | Failure → root cause → validated patch | `diagnose` action, `ExecutionLog`, RunPanel | `runtime/diagnose.py` + patch validator + safe test |
| Voice Interface | STT/TTS, multilingual, confirmation | `useSpeechInput.ts` | TTS adapter, locale routing, spoken confirmation tokens |
| Approval Manager | Human-in-the-loop | `approvals/*`, approval node | Durable waits table + resume worker hook |

---

## 3. Chat control-plane flow

```
Chat message
  → Intent Parser            (typed action candidates + confidence)
  → Context Resolver         (workflow, node, execution, credential from @mentions)
  → Permission Check         (services/authorization.py — same as HTTP)
  → Action Plan              (mutations[] + expected side effects)
  → Preview                  (diff card, graph preview, confirmation token)
  → Apply                    (single editor.replace / API service call)
  → Result + Audit           (workflow version write, execution id, chat card)
```

- **Plan mode** stops after Preview; **Build mode** auto-applies non-destructive actions and requires confirmation for destructive/sensitive ones.
- Destructive set: delete workflow/version/credential, deactivate, cancel running execution, publish, external POST with side effects.
- Confirmation is a short-lived server-issued token bound to `(user, org, action hash)` so a stale chat message cannot replay it.

---

## 4. Action registry contract

```ts
type ActionDefinition<I, O> = {
  name:
    | 'workflow.create' | 'workflow.update' | 'workflow.delete' | 'workflow.duplicate'
    | 'workflow.rename' | 'workflow.import' | 'workflow.export'
    | 'workflow.publish' | 'workflow.unpublish'
    | 'node.add' | 'node.update' | 'node.remove' | 'node.connect' | 'node.disconnect'
    | 'node.configure' | 'graph.auto_layout'
    | 'execution.run' | 'execution.test' | 'execution.cancel' | 'execution.resume'
    | 'execution.inspect' | 'execution.diagnose'
    | 'credential.create' | 'credential.rotate' | 'credential.delete'
    | 'trigger.update' | 'schedule.update'
    | 'version.list' | 'version.restore';
  schema: ZodSchema<I>;          // also exported as the LLM tool schema
  permission: Permission;
  destructive: boolean;
  planOnly?: boolean;
  preview(i: I, ctx: ActionContext): Promise<Preview>;
  apply(i: I, ctx: ActionContext): Promise<O>;
};
```

Server mirror: Pydantic models generated from the same JSON schema; `POST /api/v1/koro/actions` validates, authorises, executes and audits.

---

## 5. API contracts (additive)

| Method & path | Purpose |
|---|---|
| `GET /api/v1/koro/actions` | Registry for UI + model tool schemas |
| `POST /api/v1/koro/actions/preview` | Dry-run a plan (diff + required credentials) |
| `POST /api/v1/koro/actions/apply` | Execute an approved plan with confirmation token |
| `GET /api/v1/workflows/{id}/triggers` | Activation state, webhook URLs (test/prod), next run times |
| `POST /api/v1/workflows/{id}/executions/{exec}/cancel` | Cancel running execution |
| `POST /api/v1/executions/{id}/resume` | Resume a durable wait/approval |
| `GET /api/v1/executions/{id}/timeline` | Node timeline + slowest node + errors |
| `POST /api/v1/executions/{id}/diagnose` | Root cause + validated patch proposal |
| `POST /api/v1/webhooks/{triggerId}/test` | Test webhook URL (non-production) |
| `GET/POST /api/v1/workflows/{id}/versions` | Existing version list + restore |

All routes: org scoping, RBAC via `services/authorization.py`, CSRF for mutations, audit rows, no secret values in responses.

---

## 6. Data model changes (additive, Alembic)

| Table | Change | Reason |
|---|---|---|
| `workflow_versions` | add `definition_version` default `1.0`; keep `status` | Explicit schema version per version row |
| `workflow_executions` | add `timeout_at`, `cancel_requested_at`, `idempotency_key` (unique per workflow) | Timeout, cancel, duplicate-event prevention |
| `execution_events` | add `node_id`, `item_index`, `duration_ms`, `payload` (redacted) | Item-level timeline |
| `workflow_waits` (new) | `execution_id`, `node_id`, `resume_token`, `resume_at`, `payload` | Durable wait/approval resume |
| `webhook_deliveries` (new) | `trigger_id`, `delivery_id` unique, `received_at`, `status` | Replay protection, delivery audit |
| `workflow_schedules` (optional) | normalized cron rows | Scheduler leader election, next-run queries |

Relationships: `workflows 1—n workflow_versions 1—n workflow_executions 1—n execution_events`; `workflow_executions 1—n workflow_waits`; `workflows 1—n webhook_deliveries` via trigger id (JSON trigger reference validated by definition contract).

---

## 7. Event schema

```json
{
  "event": "node.failed",
  "execution_id": "uuid",
  "workflow_id": "uuid",
  "version_id": "uuid",
  "organization_id": "uuid",
  "node_id": "n_12",
  "node_name": "HTTP Request",
  "item_index": 3,
  "attempt": 2,
  "status": "failed",
  "duration_ms": 812,
  "error": { "code": "HTTP_500", "message": "upstream rejected", "retriable": true },
  "ts": "2026-11-22T09:00:00Z"
}
```

Rules: no secret values; payload redacted by `core/logging.py` rules; events persisted before publish; chat cards subscribe to the same stream used by the run panel.

---

## 8. Item model and expression runtime (shared contract)

- `Item = { json: object, binary?: Record<string, BinaryRef>, pairedItem?: number | number[], error?: {code,message} }`.
- Engine adapters accept legacy dict/list input and normalize to `Item[]`; output is always `Item[]` per port.
- Expression context: `$json`, `$input.first/last/all`, `$node["Name"]`, `$vars`, `$execution`, `$workflow`, `$now`.
- One grammar file checked into `packages/expression-spec` (JSON test vectors); Python and TS implementations must pass it in CI.

---

## 9. Security architecture

- Chat actions resolve targets server-side from ids; never trust model-supplied org/user ids.
- Credentials decrypted only inside the execution/credential service call; expressions never receive decrypted values, only ids/`$vars`.
- HTTP/Webhook nodes must route through `connectors/netsec.py` for SSRF checks (today the engine's `httpx` path needs to be wrapped).
- Expression evaluation: backend uses a restricted evaluator (no imports, no attribute access outside the scope); web preview is explicitly non-authoritative.
- Confirmation tokens + permission checks for destructive actions; every apply writes `audit_logs`.
- Rate limits per org for chat actions and executions; per-workflow concurrency caps.

---

## 10. Rollout

1. **Foundations**: item model, expression spec, action registry, registry generation.
2. **Operations**: durable waits, cancel/timeout, error workflows, webhook HMAC.
3. **Chat parity**: every UI action exposed as a registry action; UI buttons become thin clients of the same actions.
4. **Scale**: Redis queue behind `OPENAGENT_EXECUTION_QUEUE=redis`; in-process default unchanged.
5. **Polish**: NDV expression toggle, graph preview in chat, voice TTS.

Each step ships with tests and keeps existing workflows and SQLite local mode working.
