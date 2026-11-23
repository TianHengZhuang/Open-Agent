# Feature Parity Matrix — n8n-inspired audit

**Audit date:** 2026-11-22  
**Scope:** the OpenAgent workflow product in this repository (`apps/api`, `apps/web`).  
**Method:** source inspection plus the existing automated suites; no feature is marked
EXISTS because it is planned or documented elsewhere. The baseline is the n8n
workflow product, not visual similarity.

**Current verification baseline:** API **1421 passed / 1 skipped**, web **272 passed
(43 files)**, TypeScript **0 errors**, production build **pass**. These tests prove
the behaviour below; they do not prove the missing rows.

Legend:

| Status | Meaning |
|---|---|
| **EXISTS** | A user-facing capability is implemented and exercised by tests. Gaps are polish, scale or edge cases. |
| **PARTIAL** | The core path works, but a material n8n capability, safety property or UI is absent. |
| **MISSING** | No working implementation; types or docs alone do not count. |
| **NEEDS REFACTOR** | The capability exists but its current design blocks correctness or parity and must be replaced/consolidated. |

**Summary:** 8 EXISTS · 13 PARTIAL · 1 NEEDS REFACTOR · 2 MISSING.

---

## 1. Comparison matrix

| # | Feature | Status | n8n benchmark | Current implementation / evidence | Material gap | Priority |
|---|---|---|---|---|---|---|
| 1 | Workflow definitions | **EXISTS** | Versioned JSON graph with settings, static data, pin data, metadata | Canonical schema **1.1** in `services/workflow_schema.py` and `features/workflows/types.ts`; triggers, nodes, typed edges, variables, `settings.timezone`, `settings.execution_timeout_seconds`, `error_workflow_id`, `static_data`, `metadata`; Pydantic + Zod validation; immutable version rows; publish/draft state | `error_workflow_id` and global timeout are stored/validated but not consumed by the runtime (tracked under error handling/engine); no pin data | — |
| 2 | Node definitions | **EXISTS** | Registry of node types with versions, properties, inputs/outputs | One source `node-catalog.ts` (42 nodes + 5 triggers) → generated web + API `node-registry.json`; `registry/node_registry.py`; `/nodes/registry` and per-type endpoints; typed `input_ports`/`output_ports`, config fields, defaults, versions; drift test compares both copies byte-for-byte | `typeVersion` is a single version per node; no node-level migration chain or custom-node SDK | — |
| 3 | Node parameters | **PARTIAL** | Rich parameter types, conditional display, dynamic options, loadOptions, resource locators, per-field errors | `configFields` (text, number, select options, boolean, code, credential), required flags, defaults, numeric bounds; `Inspector.tsx`; shared backend config validation | No `displayOptions`/depends-on visibility, dynamic/remote option loading, credential/resource pickers, expression-vs-fixed toggle, field-level server errors, multi-line/rich editors | P1 |
| 4 | Connections | **EXISTS** | Typed main/error connections, branches, fan-in/out | Named ports generated from the catalog; `from_port`/`to_port`/`connection_type`/`metadata`; port compatibility matrix; duplicate/fan-in/fan-out/required-input checks in Python and TS; canvas renders port geometry; n8n port mapping | Error ports are modelled but no builtin node exposes one, so error edges are rejected; no node currently has multiple input ports; item-level routing is not implemented | — |
| 5 | Expressions | **NEEDS REFACTOR** | One expression language with `$json`, `$node`, `$input`, `$vars`, `$env`, preview and server evaluation | Two evaluators: browser JS in `lib/engine/expressions.ts` (preview) and a separate Python mini-resolver in `runtime/local_engine.py`; reference-name checking in `workflow_definition.py` | Grammars, helpers and failure semantics differ; no shared test vectors; backend does not support the browser `$input`/`$node[...]` forms; `{{env.NAME}}` reads arbitrary process environment (no allowlist); `{{credentials.*}}` is accepted by validation but not resolved by the engine | **P0** |
| 6 | Credentials | **PARTIAL** | Encrypted credential store, typed credentials, OAuth, per-environment binding, credential test | `connectors/crypto.py`; masked list; create/rotate/revoke/delete in `api/v1/integrations.py`; OAuth connect flow; connector engine decrypts credentials for connector actions; validation requires `credential_id`/`{{...}}` references instead of literals | **Workflow execution never resolves `credential_id`** (no credential access in `local_engine.py`), and connector action nodes are skipped, so a graph node cannot authenticate with a stored credential; no per-environment scope or credential test; builder only offers a text `credential_id` field, not a picker | **P0** |
| 7 | Trigger types | **PARTIAL** | Manual, webhook, schedule, polling, event, error, chat, form | Manual, webhook, schedule, event, chat triggers in the catalog/types and runtime; webhook activation tied to publish | No polling trigger, **no error trigger**, no form trigger; activation lifecycle is limited to publish/unpublish; no trigger-level test listener or concurrency policy | P1 |
| 8 | Webhooks | **PARTIAL** | Stable production/test URLs, auth, HMAC, replay protection, delivery history | `api/v1/workflow_hooks.py`: active-workflow lookup, path routing, shared-secret check with `hmac.compare_digest`, synchronous execution | No timestamp/nonce or replay protection, no body-signature scheme, no idempotency/delivery log, no separate test URL, no per-method/configurable response, no rate limit | P1 |
| 9 | Scheduling | **EXISTS** | Cron/interval schedules, timezone, activation, misfire policy | `runtime/schedule_runner.py`: cron matching, per-workflow timezone with `zoneinfo`, once-per-minute dedupe, runner loop | No leader election/DB lock for multiple API instances, no catch-up/misfire policy, no next-run preview, no schedule-specific concurrency limit | P2 |
| 10 | Execution engine | **PARTIAL** | Item-based execution, branching, merging, parallelism, queue mode, cancellation, checkpoints | `local_engine.py`: DAG topological run, sequential nodes, per-node timeout, retry, `on_error`, sub-workflows, approval wait, node-level test; execution APIs for start/list/detail/cancel/retry/resume/events/node-runs | No `DataItem[]`/per-item looping or `executeOnce`, no parallel branch execution, **global workflow timeout is not enforced**, cancel only writes DB status (not cooperative), no durable checkpoint or real queue, merge semantics are not item-aware | **P0** |
| 11 | Error handling | **PARTIAL** | Stop/continue, error output, error workflow, per-item errors, dead letter | `on_error: stop/continue`, failed-node records, retry, execution error fields | `error_workflow_id` is validated/stored but never invoked; no node error output runtime; no dead-letter queue or replay; no error-trigger workflow | P1 |
| 12 | Retry policies | **PARTIAL** | Configurable attempts, delay/backoff, per-node and workflow retry | `retry_policy.max_attempts` (0–10), exponential `0.25 * 2^n` capped at 2s, Inspector field; execution-level retry endpoint | Backoff is fixed and not configurable; no retry-on-status/error filter, no per-item retry, no workflow-level retry policy, no jitter/deadline | P1 |
| 13 | Execution history | **EXISTS** | Run list, filters, detail, node data, retention, delete/export | `workflow_executions`, `execution_events`, node runs; list/detail/events/node-runs APIs; `History.tsx` run table; run detail page | No retention/pruning policy, full-text/error search, saved filters, bulk delete/export, or side-by-side run compare; large payload storage has no cap | P1 |
| 14 | Workflow versions | **EXISTS** | Immutable versions, diff, restore, publish | `workflow_versions`; list/get/restore/publish/unpublish; `VersionHistory` with preview + restore; executions pin `workflow_version_id` | No visual/text diff, named versions, version retention or "restore as new workflow"; restore is whole-definition only | P1 |
| 15 | Sub-workflows | **EXISTS** | Call another workflow with defined inputs/outputs | `subworkflow` node + `_exec_subworkflow`; max depth 5; call-stack cycle rejection; DB session required; execution linkage | No input/output mapping UI or contract validation, no cross-project/org sharing, no async/fire-and-forget mode, no sub-workflow execution tree in the run UI | P2 |
| 16 | AI agents | **PARTIAL** | Agent node with model, memory, tools, parser sub-nodes | Separate agent platform (`runtime/agent_*`, `/agents`, chat/tools) exists and is tested; the workflow catalog has `agent` and related nodes | **The workflow engine does not execute `agent` nodes**: they are returned as skipped with "requires an agent"; no model/memory/tool/parser sub-node ports; agent runs are not first-class workflow steps | P2 |
| 17 | Human approval | **PARTIAL** | Durable wait, approve/reject, timeout/escalation, resume at the paused step | `approval` node returns `waiting`; approval records + APPROVED gate; resume endpoint validates org/execution/approval, consumes single-use approval, re-queues execution | Resume calls `start_execution` and replays the plan from the beginning; **no checkpoint/resume-at-node**, so side effects before the gate can repeat; no approval timeout/escalation; no per-step approval UI/audit trail in the run view | P1 |
| 18 | Integrations | **EXISTS** | Large connector catalog with auth, actions, triggers, rate limits | Connector manifests/catalog, actions/triggers/capabilities/search, custom connector registration, OAuth, encrypted credentials; `api/v1/integrations.py` | Breadth is far below n8n; no standardized rate-limit/retry descriptors, dynamic resource discovery, or marketplace-style versioning | P2 |
| 19 | Binary data | **MISSING** | Binary items with storage, preview, download, passing between nodes | Only contract types (`DataItem.binary`, `BinaryReference`) in TS/Python; no binary in the engine, no storage adapter, no upload/download/preview, no node support | Everything: binary item model, object storage, lifecycle/retention, MIME handling, node inputs/outputs, UI preview | P2 |
| 20 | Debugging | **PARTIAL** | Step execution, pinned/mock data, breakpoints, expression preview, per-node rerun | Run panel, execution events, node runs, test-single-node, Koro `diagnose` action, structured errors | No step-through/breakpoints, pinned data/mocks, expression preview with real context, safe node re-run from a checkpoint, or root-cause → validated patch → re-test loop | P1 |
| 21 | Import/export | **PARTIAL** | Full n8n JSON import/export, credentials/settings, version mapping, round-trip | OpenAgent envelope export/import; `n8n-format.ts` maps known types/parameters, preserves branch ports, reports skipped sub-nodes and unknown types as warnings; typed edge fields round-trip | No complete type/parameter map, `typeVersion` migration, credential binding, pin data/settings fidelity, sub-node graph reconstruction, or import report UI; unknown nodes collapse to transform | P2 |
| 22 | Environment configuration | **PARTIAL** | Environment variables, external secrets, per-environment credentials, promotion | Workflow `variables` resolved by the engine; `{{env.NAME}}` supported; org settings; Docker/env configuration; publish separates draft from active | `{{env.*}}` exposes the whole process environment (including app secrets) with no allowlist; no per-environment credential/variable sets, promotion workflow, or secret-manager integration | **P0** (allowlist) / P1 (environments) |
| 23 | Production deployment | **PARTIAL** | Queue mode/workers, HA, metrics, graceful shutdown, backups | Docker Compose (dev + production), Alembic migrations, health checks, JWT/CORS/rate-limit middleware, structured logging, publish lifecycle | In-process execution only (no Redis/worker queue), no graceful drain or backpressure, no HA scheduler, no metrics/tracing/SLOs, no backup/restore or data-retention runbook, no horizontal-scale test | P1/P2 |
| 24 | Workflow collaboration | **MISSING** | Shared workflows, roles, comments, concurrent editing safeguards | Single-editor autosave with optimistic concurrency/version checks; org RBAC on API routes | No presence, comments/mentions, multi-user locks, change attribution per node, or review/approval of workflow edits | P3 |

---

## 2. What changed since the previous audit

- **Workflow definitions** moved from a loose JSON contract to a canonical
  1.0/1.1 schema shared by Python/TypeScript, with version pinning.
- **Node definitions** are now generated from one catalog (42 nodes + 5 triggers)
  into both runtimes; the old duplicate/undocumented registry is gone.
- **Connections** moved from labels only to typed ports with compatibility,
  cardinality, duplicate and required-input validation on both sides.
- **Credentials** gained rotation/revoke/OAuth management, but execution binding
  is still absent; this is now the higher-priority gap.
- **Error workflow**, **timezone** and **execution timeout** fields were added to
  the definition, but only timezone is consumed by the scheduler; the other two
  are still contract-only.

## 3. Cross-cutting findings

1. **Two runtimes, one contract, incomplete consumption.** Schema and registry are
   shared, but the Python engine does not yet consume credential, error-workflow,
   item/binary or trace contracts. Contract-only fields must not be presented as
   working features.
2. **The n8n differentiator is item execution.** Most gaps (branching, merge,
   per-item errors, binary, agent steps, retries, debugging, approvals) become
   simpler once a real `DataItem[]` execution model exists. Build that before
   adding more node types.
3. **Expression parity is a security issue, not only a UX issue.** The browser and
   server evaluate different languages, and `env` access is unallowlisted. Unify
   semantics and restrict secrets before production use.
4. **Approval resume is not durable.** Re-queuing from the start can replay side
   effects. A checkpoint + resume token is required for safe human-in-the-loop
   workflows.
5. **Production scale is deliberately simple.** In-process execution, DB-status
   cancellation and no scheduler lock are acceptable for a single instance, not
   for HA or high-value workloads.

---

## 4. Prioritised implementation plan

Priorities reflect **risk × dependency**, not feature count. Sizes are indicative:
**S** ≤ 1 week, **M** 1–3 weeks, **L** > 3 weeks for one experienced engineer,
including tests and migration.

### P0 — correctness, security and the execution foundation

These block safe production use and most later parity work.

| Order | Work item | Why now | Exit criteria | Size | Depends on |
|---|---|---|---|---|---|
| P0.1 | **Unify expressions** into one documented grammar with a Python reference implementation and a browser mirror; add shared JSON test vectors; implement `$json`, `$node`, `$input`, `$vars`, `$env`, `$credentials` consistently; **allowlist `env` names** and reject app secrets | Split evaluators already produce different results; env exposure is a security defect | Same vectors pass in API and web; unsupported syntax fails identically; `{{env.SECRET_KEY}}` is rejected outside an explicit allowlist; no expression can read non-allowlisted process env | M | — |
| P0.2 | **Credential resolution at execution**: load `credential_id` server-side, decrypt only inside the node adapter, bind to org/environment, redact logs/errors, never export values; add credential test endpoint | Credentials can be stored but not used by workflow nodes, so the feature is operationally incomplete and encourages literal secrets | HTTP/connector nodes authenticate via a stored credential; cross-org credential use is rejected; exports and execution payloads contain only references; tests cover rotate/revoke during a run | M | P0.1 for reference syntax |
| P0.3 | **Item execution core**: `DataItem[]` through nodes, per-item mapping, `pairedItem`, `executeOnce`, error items, merge/fan-in semantics; migrate builtin nodes incrementally | Every n8n-class capability downstream (binary, agents, retries, debugging) assumes items; current single-context execution cannot express them | A multi-item workflow maps/filters/merges correctly; `pairedItem` survives branches; per-item failure is recorded without aborting the batch; legacy workflows produce one item and behave unchanged | L | Node registry (done) |
| P0.4 | **Execution lifecycle safety**: enforce `settings.execution_timeout_seconds`, cooperative cancellation between nodes/items, durable checkpoints and resume-at-node for approvals | Current timeout is unused, cancel is DB-only, and approval resume replays from the start | A timed-out run stops within the budget; cancel reaches `cancelled` within one node boundary; approval resume continues after the gate without re-running completed side effects; crash/restart test passes | L | P0.3, approval API |

**P0 gate:** do not market workflows as safe for production side effects until
P0.1–P0.4 are complete and covered by integration tests.

### P1 — production-grade parity

| Order | Work item | Why | Exit criteria | Size |
|---|---|---|---|---|
| P1.1 | **Error workflows + error outputs + dead letter**: invoke `error_workflow_id`, expose `error` port on nodes that can fail, persist failed payloads for replay | n8n's core reliability feature; field exists but is inert | A failing run triggers the error workflow once with context; error edges receive failed items; failed payloads are listable/replayable | M |
| P1.2 | **Webhook hardening**: HMAC body signature, timestamp/nonce, replay window, idempotency key, delivery log, separate test URL | Shared-secret-only webhooks are replayable and hard to operate | Replayed/expired requests get 401/409; duplicate idempotency keys execute once; test URL runs draft without activating production; delivery history is queryable | M |
| P1.3 | **Retry policy v2**: configurable delay/backoff/jitter, retry-on filters, per-item and workflow-level policy, deadlines | Current fixed backoff cannot match provider rate limits | Policy is editable and validated; tests cover 429/5xx/timeouts; attempt history is visible per item; global deadline stops retries | M |
| P1.4 | **Dynamic node parameters**: conditional visibility, dependent fields, loadOptions/resource pickers, expression toggle, field-level errors | Static forms make the 42-node catalog hard to use correctly | At least the 10 highest-use nodes have conditional/dynamic parameters; invalid values are rejected client- and server-side with the field path | M |
| P1.5 | **Approval UX and policy**: approve/reject from run view, timeout/escalation, audit trail, per-step binding | Approval exists but is not operable at production quality | Approver sees the exact paused step and payload; timeout escalates/rejects by policy; every decision is auditable; resume is idempotent | S/M |
| P1.6 | **History/version operations**: retention policy, search/filter by status/error/date, export/delete, version diff | History exists but grows without bounds and is hard to diagnose | Configurable retention runs automatically; search returns matching runs; diff highlights node/edge/config changes; restore is audited | M |
| P1.7 | **Environment isolation**: per-environment variables/credentials, promotion (draft → staging → production), allowlisted env injection | Avoids copying secrets and editing definitions between environments | Same definition runs in two environments with different bindings; promotion never copies secret values; drift is reported | M |
| P1.8 | **Debugger basics**: pinned/mock data, step/rerun-from-node, expression preview with real context, structured failure explanation | Current debugging is read-only logs plus a diagnose action | A failed node can be re-run with pinned input without repeating upstream side effects; expression preview matches server evaluation; failure view names node/port/item and suggested fix | M |
| P1.9 | **Scheduler HA**: DB advisory lock/leader election, misfire/catch-up policy, next-run preview | Multiple API instances can double-fire schedules | Two runners never execute the same slot; restart after downtime follows the configured misfire policy; next runs are visible | S/M |

### P2 — breadth and scale

| Work item | Why | Exit criteria | Size |
|---|---|---|---|
| **Queue/worker execution** behind a feature flag (Redis or DB-backed), graceful drain, backpressure, metrics/tracing | In-process execution cannot scale or survive deploys | API enqueues; N workers execute; deploy drains without losing runs; queue depth/latency/failure metrics exist; default single-process mode preserved | L |
| **Binary data**: object storage adapter, upload/download, MIME/size limits, item preview, node support | Required for files, images and document workflows | Binary passes between nodes by reference; access is org-scoped and expiring; preview/download works; retention cleans orphaned objects | L |
| **AI agent as a workflow step**: invoke agent runtime from `agent` node, model/memory/tool/parser sub-node ports, streaming/trace linkage | Agent platform exists separately; workflows skip agent nodes | An agent node executes with configured model/tools; run view shows agent turns/tool calls; approvals gate tool use; failures surface as node errors | L |
| **Integration descriptors**: rate limits, retry hints, pagination, dynamic resources, versioned manifests | Catalog breadth is not enough without operational metadata | Descriptors are validated and drive retries/pagination; at least five connectors implement them end-to-end | M |
| **Sub-workflow UX**: input/output contract, mapping editor, execution tree, cross-project sharing | Current subflows work but are opaque | Editor validates mappings; run view nests child runs; cycles/depth errors are actionable | M |
| **Trigger breadth**: polling triggers with cursor/backoff, error trigger, form trigger | n8n trigger parity | Polling resumes from a durable cursor without duplicates; error trigger fires once per failed run; form submissions create runs with validated payloads | M/L |
| **Import/export fidelity**: full type/parameter map, `typeVersion`, sub-nodes, settings/pin data, credentials as references, report UI | Current importer silently degrades unknown nodes | A representative n8n corpus round-trips with an explicit loss report; unsupported fields are listed, never silently dropped | M |

### P3 — collaboration and ecosystem

| Work item | Why | Exit criteria | Size |
|---|---|---|---|
| **Workflow collaboration**: presence, comments/mentions, edit locks or CRDT, per-node attribution, review flow | Needed for teams; not required for single-operator use | Two editors see presence and avoid lost updates; comments resolve per node; review/approval gates publish | L |
| **Custom node SDK/marketplace** (only if the product direction requires it) | n8n ecosystem advantage; high support/security cost | Sandboxed node package contract, versioning, signing, install/disable, compatibility tests | L |

---

## 5. Recommended sequence

1. **Wave 1 (P0):** expressions → credentials → item core → lifecycle safety.
2. **Wave 2 (P1):** error workflows → webhooks → retries → approvals → parameters →
   history/version operations → environment isolation → debugger → scheduler HA.
3. **Wave 3 (P2):** queue/workers → binary → agent step → integration descriptors →
   subflow UX → trigger breadth → import/export fidelity.
4. **Wave 4 (P3):** collaboration → custom node ecosystem.

Do not start P2 breadth before P0.3: adding nodes on a single-item engine repeats
the same rework. Do not expose `env` or credential references to production before
P0.1/P0.2.

## 6. Production-readiness verdict

- **Suitable today:** single-instance, internal, low-risk automations with manual or
  scheduled triggers, no stored-credential use, no binary data and no human gate
  with side effects.
- **Not yet suitable:** multi-tenant or high-value workflows, provider
  authentication through stored credentials, regulated approvals, file processing,
  HA scheduling, or horizontal scaling.
- **Biggest parity gap:** per-item execution; **biggest safety gap:** expression
  environment access and missing credential resolution.

## 7. Verification rules for this matrix

- A row may move to **EXISTS** only when an automated test covers the user-facing
  behaviour, not merely the type/schema.
- Contract-only fields must be described as such and listed under the consuming
  feature's gap.
- Re-run the API/web suites, typecheck, build, registry consistency and Ruff after
  any change that moves a row.
- Keep this document in sync with `docs/CONNECTION_DATA_FLOW_CONTRACT.md` when
  connection/item semantics change.
