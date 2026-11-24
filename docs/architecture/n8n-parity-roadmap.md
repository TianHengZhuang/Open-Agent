# n8n parity — execution roadmap (Phases 7–14)

Status: **active plan**. Phases 1–6 of
[`chat-first-n8n-parity.md`](./chat-first-n8n-parity.md) are implemented
(expression engine, item contract, error workflow/pins/partial execution,
concurrency/pruning/limits, node registry, chat control plane). **Phase 7 is
implemented** (node-level retry, `onError` continue/error-output, executeOnce,
alwaysOutputData, disabled passthrough, per-node timeout, and the
`set_node_settings` chat tool). **Phase 8 is implemented** (binary item
properties, local binary store with retention, convert/extract/file nodes,
webhook uploads, `$binary`, binary pass-through). **Phase 9 is implemented**
(sub-workflow execution rows and the parent→child tree, cancellation cascade,
`Execute Workflow` trigger, workflow static data with
`$getWorkflowStaticData`, and the `link_subworkflow` / `set_static_data` chat
tools). **Phase 10 is implemented** (a sandboxed Code node: real Python and
JavaScript in a throwaway guarded process, Docker required in production,
expression mode unchanged). **Phase 11 is implemented** (node coverage in
tiers: eight new local core nodes, a tier/status map over every registry node,
`GET …/nodes/coverage`, and a chat `node_coverage` action that answers "can you
do X?" honestly). **Phase 12 is implemented** (production operations: queue
mode with a claimable execution row and a worker entry point, production vs
test webhook URLs with duplicate-path rejection, `save_execution_data` modes,
a crash journal that fails runs whose owner stopped beating, a Prometheus
`/metrics` endpoint, and a graceful shutdown drain). **Phase 13 core is
implemented** (a single `TOOL_SCHEMAS` table driving the system prompt, the
deterministic parser and `GET …/copilot/tools`; signed plan→confirm→apply
tokens for destructive tools; `undo_last`; and export/import, node logs,
resume-wait, prune and health tools). **Phase 14 is implemented** (the UI
collapses to six surfaces: 114 routes → 28 real pages plus 86 forwarding
routes driven by one `features/navigation/surfaces.ts` map, a five-entry
sidebar, and an `open_page` chat tool that returns the page for a workflow or
run). Credential writes, agent lifecycle, memory, MCP and marketplace chat
tools remain. This document is the audited continuation: what
n8n master actually has, what Open Agent still lacks, and the exact order to
close it — engine first, then operations, then the UI collapse into one chat
box.

---

## সংক্ষিপ্ত সারাংশ (Bangla)

`reference/n8n`-এ n8n master (`bf31087`, 2026-11-22) sparse clone আছে —
`packages/workflow`, `packages/core`, `packages/cli/src`,
`packages/nodes-base/nodes` (৩০৮টি নোড ডিরেক্টরি) ও `packages/editor-ui/src`।
আমাদের বর্তমান অবস্থা: ৫৯ নোড + ৬ ট্রিগার, ১১৪টি ওয়েব পেজ, ৪০টি API রাউটার
মডিউল, শক্তিশালী কিন্তু অসম্পূর্ণ ইঞ্জিন (retry/binary data/sub-workflow/queue
mode নেই)।

এই রোডম্যাপ ৮টি ফেজে (৭–১৪) বাকি কাজ ভাগ করে: নোড-লেভেল সেমান্টিক্স →
বাইনারি ডেটা → সাব-ওয়ার্কফ্লো → কোড স্যান্ডবক্স → নোড কভারেজ টায়ার →
প্রোডাকশন অপারেশনস (queue mode/webhook lifecycle/metrics) → চ্যাট কন্ট্রোল
প্লেন ১০০% → UI কোলাপ্স (১১৪ পেজ → ৬ সারফেস)। প্রতিটি ফেজে ফাইল-লেভেল
পরিবর্তন, n8n রেফারেন্স সোর্স, অ্যাকসেপ্টেন্স ও টেস্ট কমান্ড দেওয়া আছে।

> **লাইসেন্স সীমা:** n8n fair-code (Sustainable Use License)। আমরা কোড কপি
> করি না — আচরণ (behavior) পড়ে clean-room পুনরায় লিখি। রেফারেন্স শুধু
> স্পেসিফিকেশনের জন্য।

---

## 1. Reference checkout and method

| Item | Value |
| --- | --- |
| Path | `reference/n8n` (git-ignored, 262 MB) |
| Commit | `bf31087be421ee4352ef87471546488d357aaa81` (2026-11-22) |
| Sparse paths | `packages/@n8n`, `packages/cli/src`, `packages/core`, `packages/editor-ui/src`, `packages/nodes-base/nodes`, `packages/workflow` |
| Node library size | 308 node directories under `packages/nodes-base/nodes` |
| Refresh | `cd reference/n8n && git pull --ff-only` (sparse checkout already configured) |

Method for every phase: read the n8n source contract → write the behavior spec
in this repo → implement in Python/TypeScript → add tests that encode the
contract → update this document with what was actually verified.

---

## 2. Audited gap matrix

Legend: ✅ implemented, ◐ partial, ✗ missing.

### 2.1 Execution engine

| Capability | n8n source | Open Agent | Plan |
| --- | --- | --- | --- |
| `{json, binary, pairedItem}` item envelope | `packages/workflow/src/interfaces.ts` | ✅ json + pairedItem + `binary` | — |
| `{{ }}` expression grammar (native AST, limits) | `packages/workflow/src/expressions/native-evaluation/*` | ✅ Python port | — |
| Data proxy `$json/$node/$()/$items/$input/$vars/$now/$today/$workflow/$execution/$parameter/$binary` | `packages/workflow/src/workflow-data-proxy.ts` | ✅ | — |
| Per-node retry (`retryOnFail`, `maxTries`, `waitBetweenTries`) | `packages/core/src/execution-engine/workflow-execute.ts` | ✅ legacy `retry_policy` + n8n fields | — |
| `onError: continueErrorOutput` (2nd output) | same | ✅ error item on the `error` port; handled failures keep the run green | — |
| `alwaysOutputData`, `executeOnce`, disabled nodes | same | ✅ | — |
| Run-mode per item vs all items | node `execute` vs `executeSingle` contexts | ✅ `run_mode: all \| each` | — |
| Node timeout | `executionTimeout` / node settings | ✅ `timeout_seconds` + `NODE_TIMEOUT` | — |
| Partial execution / pin data | `partial-execution-utils/*` | ✅ | — |
| Wait node + webhook/form resume | `packages/core/src/execution-engine/waiting-*` | ✅ timed + webhook resume | — |
| Loop over items (SplitInBatches + `$itemIndex`) | nodes-base `SplitInBatches` | ◐ batch split, no loop-back semantics | Phase 7 |
| Sub-workflows (Execute Workflow node + trigger) | nodes-base `ExecuteWorkflow`, cli sub-workflow service | ✅ child execution rows + tree, depth 10, cycle-safe, cancel cascade, `execute_workflow` trigger | — |
| Code node with real JS/Python | nodes-base `Code` | ✅ sandboxed Python/JS runner (subprocess dev, Docker prod), expression mode kept | — |
| Static data per workflow | workflow static data helpers | ✅ `workflows.static_data`, `$getWorkflowStaticData(scope)`, chat write | — |
| Binary data manager (S3/R2/local) | `packages/cli/src/binary-data` | ✅ local store + retention | — |
| Error workflow | ✅ settings.error_workflow_id | ✅ | — |
| Queue mode (main + workers) | `packages/cli/src/queue-mode/*`, `scaling` | ✅ `WORKFLOW_QUEUE_MODE` + claimable rows + worker CLI | — |
| Execution pruning / retention | `executions` retention | ✅ | — |
| Concurrency limits | `concurrency` package | ✅ per workflow | — |
| Crash recovery / journal | `crash-journal.ts` | ✅ journal + heartbeat sweep → `EXECUTION_CRASHED` | — |
| Webhook test vs production URLs + lifecycle | `active-workflow-manager`, `webhooks` | ✅ `/hooks` vs `/hooks-test`, duplicate path refused on publish | — |
| Metrics (Prometheus) | `packages/cli/src/metrics` | ✅ `/metrics` text alongside `/operations/workflows` JSON | — |
| Save execution data (`all \| none \| on_error`) | `executions/execution-data/*` | ✅ `settings.save_execution_data`, events always kept | — |

### 2.2 Node breadth

| Tier | n8n | Open Agent now | Plan |
| --- | --- | --- | --- |
| Core/flow nodes | ~35 (Set, If, Switch, Merge, Filter, Sort, Limit, Aggregate, RemoveDuplicates, CompareDatasets, SplitInBatches, Wait, Code, HTTP, Webhook, Schedule, RespondToWebhook, ExecuteWorkflow, Error trigger, DateTime, Crypto, HTML, XML, JSON, Compression, NoOp, StopAndError…) | ✅ 33 Tier A local nodes (incl. Compare Datasets, Date & Time, Crypto, HTML, XML, Compression, No Op, Stop and Error) | — |
| File/binary nodes | Read/Write Binary File, Convert to File, Extract from File, Spreadsheet, FTP, S3 | ✅ convert/extract/read/write (CSV/JSON/text) | FTP/S3 via HTTP/connector |
| App integrations | ~273 provider nodes | ◐ 11 connector providers exposed as `connector_action` (Gmail, Slack, GitHub, GitLab, Google Calendar/Drive, Postgres, Discord, Telegram, Notion, HubSpot, generic HTTP) | — |
| AI nodes | OpenAI, Anthropic, LangChain agent/chain/memory/vector store nodes | ◐ provider-backed, skipped locally (`prompt`, `agent`) | — |
| Long tail | 300+ | documented in the coverage endpoint as HTTP/MCP/extension (22 named capabilities) | — |

### 2.3 Surfaces

| Surface | n8n | Open Agent | Plan |
| --- | --- | --- | --- |
| Primary UI | Canvas-first, many pages | 28 pages + 86 forwarding routes, five-entry sidebar | ✅ Phase 14 |
| Assistant | n8n Assistant (build/debug from chat) | ✅ `/assistant`, global dock, voice input | Phase 13 |
| Chat actions | Tool use over the API | ✅ 46 declared tools: one registry feeds the prompt, the parser and `GET …/copilot/tools`; confirmations + undo | Domain gaps listed in 10.1 |

---

## 3. Ground rules

1. **Behavior, not source.** No n8n code is copied. n8n is fair-code
   (Sustainable Use License); every port is a clean reimplementation from the
   observed contract, and node names/parameters are factual API surface.
2. **Everything reachable from chat.** Any capability added in Phases 7–12 must
   ship a chat tool in the same phase (see Phase 13 table). A UI control is
   optional; the chat action is mandatory.
3. **No silent fake.** Provider-backed nodes stay `skipped` with a reason when
   credentials/network are absent — never return fabricated success.
4. **One source of truth for nodes.** `node-catalog.ts` → generated
   `node-registry.json`; both validators read it.
5. **Every phase ends green**: touched-file `ruff`, focused `pytest`,
   `npm run typecheck`, `vitest src/features/workflows`.

---

## 4. Phase 7 — Node-level execution semantics

Goal: a node behaves like an n8n node, not like a single function call.

n8n reference:

* `packages/core/src/execution-engine/workflow-execute.ts` — `executeNode`,
  `NodeExecuteFunctions`, retry loop, `continueOnFail`/`alwaysOutputData`.
* `packages/core/src/execution-engine/node-execution-context/execute-context.ts`
  — `getInputData`, `helpers`, item indexing.
* `packages/workflow/src/interfaces.ts` — `INodeExecutionData`, `NodeExecutionData`.

Changes:

| File | Change |
| --- | --- |
| `apps/api/src/openagent/services/workflow_schema.py` | accept `retry_on_fail`, `max_tries`, `wait_between_tries_ms`, `on_error` (`stop` \| `continue` \| `continue_error_output`), `always_output_data`, `execute_once`, `timeout_ms`, `disabled` |
| `apps/web/src/features/workflows/types.ts` + `validate.ts` + `node-catalog.ts` | same fields; `on_error` enum; second output port for `continue_error_output` |
| `apps/api/src/openagent/runtime/local_engine.py` | retry loop with exponential backoff and per-node timeout; `execute_once` caches first item output; disabled nodes pass input through; error item appended to port 2 (`main[1]`) for `continue_error_output`; `always_output_data` emits `[{}]` when empty |
| `apps/api/src/openagent/runtime/executors/nodes/*.py` | expose per-item mode metadata; `split_in_batches` gains loop-back edge support |
| `apps/api/tests/test_workflow_nodes.py` | retry success-after-failure, retry exhaustion → error item, continue-error-output routing, executeOnce dedupe, disabled passthrough, per-node timeout |

Acceptance: a node configured with `max_tries: 3` retries exactly 3 times; a
failure with `on_error: continueErrorOutput` reaches only the error branch and
the run keeps `succeeded`; a disabled node passes items unchanged; retries and
timeouts appear in the execution event log.

### 4.1 Implemented (Phase 7)

| Field (top-level on the node) | Behaviour |
| --- | --- |
| `retry_on_fail` + `max_tries` (1–10) + `wait_between_tries_ms` (0–60000) | n8n's Retry On Fail; `max_tries` counts the first attempt; wait is fixed. Legacy `retry_policy.max_attempts` still works and keeps exponential backoff. |
| `on_error`: `stop` \| `continue` \| `continueErrorOutput` | `continue` passes the input downstream; `continueErrorOutput` emits one `{error, error_code, node}` item on the `error` output, `ports: ["error"]`, `handled: true`, and the run is not failed. |
| `always_output_data` | Empty output becomes one `{json: {}}` item. |
| `execute_once` | Runs the node with the first item even when several arrive. |
| `timeout_seconds` | Per-node timeout → `NODE_TIMEOUT`; retries wrap the timeout. |
| `disabled` | Skipped with reason `DISABLED`, input passed through by validation rules. |

Engine: `apps/api/src/openagent/runtime/local_engine.py` (`_retry_plan`,
`_error_mode`, `_execute_with_policy`, `_edge_active`, run `handled` flag).
Validation: `workflow_definition.py` + web `validate.ts` (`INVALID_ERROR_HANDLING`,
`INVALID_RETRY_POLICY`, booleans). Registry: `output_ports()` adds the `error`
port only when the node asks for it, so `connection_type: "error"` edges are
legal exactly when n8n would allow them. n8n JSON import/export maps
`onError`, `retryOnFail`, `maxTries`, `waitBetweenTries`, `alwaysOutputData` and
`executeOnce` in both directions. Chat: `set_node_settings` changes any of
these fields (and `disabled`) on a named node through the validated version
path.

Tests: `apps/api/tests/test_workflow_node_semantics.py` (13) and the
`set_node_settings` mutation test in `test_koro_tools.py`.

---

## 5. Phase 8 — Binary data and files

Goal: `binary` is a first-class part of the item envelope, backed by object
storage, so file workflows work end to end.

n8n reference: `packages/cli/src/binary-data/*` (manager, object store, S3),
`workflow-data-proxy.ts` (`$binary`), nodes `ReadWriteFile`, `ConvertToFile`,
`ExtractFromFile`, `SpreadsheetFile`, `Compression`.

Changes:

| File | Change |
| --- | --- |
| `apps/api/src/openagent/runtime/items.py` | `binary: {mimeType, fileName, data|storageId, bytes}`; helpers `add_binary`, `resolve_binary` |
| `apps/api/src/openagent/runtime/binary_store.py` (new) | DB/file/R2-backed store keyed by execution id; TTL tied to execution pruning |
| `apps/api/src/openagent/runtime/expressions.py` | `$binary` proxy, `$input.item.binary`, `$items().binary` |
| `apps/api/src/openagent/runtime/executors/nodes/files.py` (new) | read/write binary, convert to/from file, extract from file, compression |
| `apps/api/src/openagent/registry/node-catalog.ts` | 6 file nodes with config fields and docs |
| `apps/api/tests/test_workflow_binary.py` (new) | round-trip base64/JSON, 10 MB cap, prune removes objects, expression access |

Acceptance: a workflow can accept a webhook upload, extract a CSV, transform
rows, and return a new file; the object store never grows past pruning; `$binary`
resolves identically in the engine and in previews.

### 5.1 Implemented (Phase 8)

| Piece | Where | Behaviour |
| --- | --- | --- |
| Binary item property | `runtime/items.py`, `runtime/binary_store.py` | `item.binary.<name> = {mimeType, fileName, fileExtension, fileSize, data\|storageId}`; `read_binary` accepts both shapes |
| Store | `runtime/binary_store.py` | `OPENAGENT_BINARY_DIR` (default `.data/binaries`), inline up to `OPENAGENT_BINARY_INLINE_BYTES` (64 KB), hard cap `OPENAGENT_BINARY_MAX_BYTES` (10 MiB), retention `OPENAGENT_BINARY_RETENTION_DAYS` (7) pruned on the scheduler tick |
| `convert_to_file` | `runtime/executors/nodes/files.py` | items → CSV/JSON/text binary, `file_name` expression, delimiter/header |
| `extract_from_file` | same | binary → items; auto format from mime/extension; CSV DictReader, JSON list/dict, text lines |
| `read_binary_file` / `write_binary_file` | same | sandboxed to `OPENAGENT_FILE_DIR` (default `.data/files`); absolute paths and `..` escapes rejected |
| Webhook uploads | `api/v1/workflow_hooks.py` | `multipart/form-data` files and any non-JSON body become a binary property (`data`, `file2`, …) on the trigger item; JSON behaviour unchanged |
| HTTP downloads | `local_engine.py` | A binary response (image/PDF/octet-stream) becomes a binary item with Content-Disposition filename instead of a lossy text body |
| Pass-through | `local_engine.py` (`_carry_binary`) | A 1:1 node between file nodes (Set, Edit fields) keeps the `binary` property |
| `$binary` | `local_engine.py` (`expression_data`) | First incoming item's binary properties; works in any expression and in previews |

Tests: `apps/api/tests/test_workflow_binary.py` (11) plus the hook upload test
in `test_workflow_operations.py`. Registry became 51 nodes + 5 triggers;
`WRITE_NODE_REGISTRY=1` regenerated both copies.

---

## 6. Phase 9 — Sub-workflows, static data, scheduling depth

Goal: reusable workflows, the way n8n's Execute Workflow node allows.

n8n reference: `packages/nodes-base/nodes/ExecuteWorkflow/*`,
`packages/cli/src/workflows/workflow.service.ts` (sub-workflow validation,
execution tree), `executeWorkflowTrigger`, `workflow static data`.

Changes:

* Node `execute_workflow` (`config.workflow_id`, `mode: run|wait`, `input_map`),
  trigger `execute_workflow_trigger` (`config.inputs`).
* `WorkflowExecution.parent_execution_id` + `root_execution_id` columns
  (migration), depth limit 10, cancellation cascades to children.
* `run_workflow` accepts an upstream payload and returns child output to the
  parent node.
* Static data: `Workflow.static_data` JSON, helpers `$getWorkflowStaticData`.
* Chat actions: `add_node` with `execute_workflow`, and
  `link_subworkflow` (resolve by name, validate no cycle) — Phase 13 table.

Acceptance: parent → child execution tree is queryable; a cycle is rejected at
validation time; cancelling the parent cancels children; child failure follows
the parent node's `on_error`.

### 6.1 Implemented (Phase 9)

| Piece | Where | Behaviour |
| --- | --- | --- |
| Execution tree | `db/models/workflow_execution.py`, migration 029 | `parent_execution_id` + `root_execution_id` (self FK, `SET NULL`, indexed); the detail endpoint returns `parent_execution_id`, `root_execution_id` and `children` (id, workflow, status, times, `parent_node`) |
| Child runs | `local_engine._exec_subworkflow` | A persisted parent creates a real child `WorkflowExecution` (`run_source: subworkflow`, depth, parent node) and stores its own `node_runs`/`output`/`summary`; the node output exposes `execution_id` |
| Cancellation cascade | `runtime/workflow_ops.cancel_execution_tree` | Cancels the run and every non-terminal descendant, BFS-bounded; used by the execution API and the chat `cancel` tool |
| Depth / cycles | `local_engine.py` | `MAX_SUBWORKFLOW_DEPTH = 10`; runtime call-stack cycle rejection (`SUBWORKFLOW_CYCLE`); chat link walks the child's latest definitions and refuses a cycle with the chain |
| Execute Workflow trigger | `_exec_trigger` | `execute_workflow` / `execute_workflow_trigger` / `subworkflow` trigger types take the caller's payload as the first item; `config.inputs` declares keys with empty defaults |
| Execution mode | `_exec_subworkflow` | `mode: wait` (default) runs synchronously; `mode: run` fails with `SUBWORKFLOW_ASYNC_UNSUPPORTED` instead of faking a background start (needs queue mode, Phase 12) |
| Static data | `workflows.static_data`, `expressions.ExpressionData.static_data` | Version `static_data` seeds the run, the workflow row's value wins, and whatever the run leaves is written back; `$getWorkflowStaticData("global")` returns the live scope dict |
| Chat tools | `koro.py` | `link_subworkflow` (resolves the child by name, links the Subworkflow node or a named one, refuses self/cycles) and `set_static_data` (scope + values, permission `workflow:update`) |

Tests: `tests/test_workflow_subworkflow.py` (child execution, cancel cascade,
static data read/persist, Execute Workflow trigger, depth) and
`test_koro_tools.py` (link mutation, chat cycle rejection, static data write).
Registry is now 51 nodes + 6 triggers (`execute_workflow`).

---

## 7. Phase 10 — Real Code node (sandboxed JS/Python)

Goal: n8n's Code node power without executing untrusted code in the API process.

n8n reference: `packages/nodes-base/nodes/Code/*` (`JavaScriptSandbox`,
`PythonSandbox`, `$input.all()`, `$input.first()`, `$input.item`, `$helpers`,
`$jmespath`), `packages/@n8n/task-runner`.

Approach: reuse the existing sandbox subsystem (`apps/api/src/openagent/sandbox`)
— a short-lived container with the items as JSON on stdin, a fixed harness that
defines `$input`, `$json`, `$helpers`, `items`, and a hard 30 s / 64 MB limit,
returning items on stdout. Python runner first; JS runner behind the same
interface.

Changes: `runtime/executors/nodes/code.py` (replace expression-only path; keep
expressions as the default `mode: "expression"`), `sandbox/runner_contract.py`,
node catalog config (`mode: runOnceForAllItems|runOnceForEachItem`,
`language: python|javascript`), tests with malicious payloads (infinite loop,
fs/network access, huge output).

Acceptance: `mode: runOnceForEachItem` runs once per item with `$json` bound;
escape attempts fail closed; no network; output size capped.

### 7.1 Implemented (Phase 10)

| Piece | Where | Behaviour |
| --- | --- | --- |
| Runner | `runtime/code_runner.py` | `OPENAGENT_CODE_RUNNER=auto\|subprocess\|docker`; production refuses anything but Docker (`CODE_RUNNER_INSECURE`); 1–300 s timeout, 256 MB default, 50k code cap, 1 MB output cap, 1000 items |
| Python harness | `runtime/code_harness.py` | Executed with `-I -B` in a temp cwd; `_items` / `_item` / `print()` per the n8n Python contract; `sys.addaudithook` blocks network, subprocess, ctypes/process control, imports of those modules, file reads outside stdlib/temp, and writes outside the sandbox; POSIX rlimits cap memory/CPU/file size |
| JavaScript harness | `runtime/code_harness.js` | `node --disallow-code-generation-from-strings` + `vm` context with `$input.all()/first()/last()/.item`, `$json`, `$helpers`, `items`; no require/process/console |
| Modes | both harnesses | `runOnceForAllItems` (default) and `runOnceForEachItem`; `all`/`each` accepted; alias `mode` falls back to the node's `run_mode` |
| Docker backend | `code_runner._docker_argv` | `docker run --rm -i --network none --read-only --memory … --cpus … --pids-limit …`, harness mounted `:ro`, only `/out` writable; images `OPENAGENT_CODE_PYTHON_IMAGE` / `OPENAGENT_CODE_NODE_IMAGE` |
| Node wiring | `runtime/executors/nodes/sandbox_code.py` | A `code` node with `language: python\|javascript` uses the runner; without `language`/with `expression` the original local expression path is untouched |
| Validation | `workflow_definition.py`, web `validate.ts`, catalog | `language` ∈ expression/python/javascript, `mode` ∈ all/each; Code node exposes both fields and documents sandbox behaviour; chat prompt lists both |

Tests: `tests/test_workflow_code.py` (12) — all-items and per-item Python,
item surface/print, expression compatibility, timeout, blocked network /
subprocess / file read, output cap, invalid return, JavaScript per-item.

Not verified here: the Docker backend was not executed in this environment
(no image pulled); the subprocess backend is development isolation, not a hard
security boundary against a CPython/vm escape — production must run Docker.

---

## 8. Phase 11 — Node coverage in tiers

Do not hand-port 308 nodes. Port by value:

1. **Tier A — core parity (~60 nodes).** Everything in n8n's "Core Nodes" and
   "Flow" groups that is local-only (flow, data transform, files, crypto,
   datetime, HTML/XML, email via SMTP/IMAP, Postgres/MySQL/MongoDB/Redis,
   S3/R2). Each gets: manifest entry, Python executor, validator coverage, one
   test, chat `add_node` support.
2. **Tier B — credential-backed providers (~35).** Reuse
   `openagent/connectors/providers/*`; expose as workflow nodes through the same
   registry so an existing connection becomes a node with zero new transport
   code. Priority: Slack, Discord, Telegram, GitHub, GitLab, Gmail, Google
   Sheets/Drive, Notion, Linear, Jira, HubSpot, Stripe, Airtable, OpenAI,
   Anthropic.
3. **Tier C — long tail (200+).** Generic HTTP Request node + MCP servers +
   the existing `.oaext` extension import (compatibility shim that maps an n8n
   community-node manifest to an extension). Documented as "use HTTP/MCP",
   not silently missing.

Add `GET /api/v1/nodes/coverage` returning tier, implemented/skipped status and
the n8n equivalent name, so the chat can answer "can you do X?" honestly.

Acceptance: every Tier A node has a passing executor test; the coverage endpoint
matches the registry; chat never claims a Tier C capability is built in.

### 8.1 Implemented (Phase 11)

| Piece | Where | Behaviour |
| --- | --- | --- |
| Eight new local nodes | `runtime/executors/nodes/core_extras.py`, `node-catalog.ts` | `crypto` (hash/HMAC/UUID/base64), `datetime` (now/parse/format/shift/diff, month-aware), `html` (text/links/table), `xml` (parse/build), `compression` (gzip/deflate/zip both ways), `compare_datasets` (same/different/only-in-A/only-in-B ports), `no_op`, `stop_and_error` |
| Tier map | `registry/coverage.py` | Every registry node gets exactly one tier: A (local, implemented), B (provider/credential/human/evaluation), C (documented long tail). A test fails when the registry and the map drift apart |
| Connector coverage | same module | The 11 bundled providers are listed as `connector_action` capabilities and cross-checked against `provider_ids()`, so an existing connection is a node with no new transport code |
| Coverage endpoint | `api/v1/nodes.py` | `GET /organizations/{org}/nodes/coverage` (requires `workflow:read`) returns `summary`, `tiers`, per-node `{tier, status, execution, n8n, note}`, `triggers`, `connectors`, `documented`, `unmapped`; `?tier=A` and `?q=crypto` filter |
| Chat action | `api/v1/koro.py` | `node_coverage` (with a deterministic "can you…/do you support…" route) answers Tier A as *built in*, Tier B as *available through connector_action/agent/model*, and Tier C as *not built in — use HTTP Request, MCP or an extension import* |
| Validation | `services/workflow_definition.py` | `stop_and_error` may terminate a branch; `compare_datasets` accepts fan-in |

Registry is now 59 nodes + 6 triggers; both copies regenerated with
`WRITE_NODE_REGISTRY=1`. Tests: `tests/test_node_coverage.py` (18 — executor
behaviour for all eight nodes, validation, tier/registry agreement, connector
parity, honest answers, endpoint auth + filters) and two chat tests in
`test_koro_tools.py`.

Not verified here: no live provider call was made (Tier B nodes stay
`skipped`/descriptor-only without a connection); the endpoint is exercised
through the FastAPI test client, not a browser; the palette does not yet show
the tier badge.

---

## 9. Phase 12 — Production operations parity

Goal: the operational surface n8n runs in production.

n8n reference: `packages/cli/src/scaling/*` (queue mode), `active-executions.ts`,
`webhooks/*`, `crash-journal.ts`, `metrics/*`, `executions/execution-data/*`.

Changes:

| Concern | Change |
| --- | --- |
| Queue mode | Route workflow runs through the existing Redis worker when `WORKFLOW_QUEUE_MODE=true`; main process enqueues `{execution_id, mode}`, worker calls `run_workflow`; the queued dispatcher from Phase 4 becomes the producer |
| Webhook lifecycle | Register/deregister per activation; test path `/hooks-test/<path>`; production path `/hooks/<path>`; reject duplicate paths; expose the resolved URL in chat |
| Execution data | `settings.save_execution_data: all \| none \| on_error` — store node outputs accordingly, keep events always |
| Crash recovery | journal active executions, mark `crashed` on boot, optional resume for waits |
| Metrics | Prometheus text endpoint `/metrics` (runs, queue depth, duration histogram, failure rate) alongside the existing JSON `/operations/workflows` |
| Shutdown | graceful drain with configurable timeout; queued runs stay queued |

Acceptance: with queue mode on, an execution completes via the worker and is
visible in `/runs`; two activations cannot claim one webhook path; a killed API
process leaves executions marked `crashed` rather than running forever; metrics
scrape shows the same numbers as the JSON endpoint.

### 9.1 Implemented (Phase 12)

| Piece | Where | Behaviour |
| --- | --- | --- |
| Queue producer | `runtime/queue_mode.py`, `local_engine.run_workflow` | With `WORKFLOW_QUEUE_MODE=true` a new run is persisted as `queued` (`metadata.queue_mode = "worker"`, definition + trigger input stored on the row) and returns immediately; nothing executes in the API process |
| Queue consumer | `runtime/queue_mode.claim_next` | Atomic claim (`UPDATE … WHERE status = 'queued'`); no two workers can run one row. Per-workflow `max_concurrency` is checked at claim time. `python -m openagent.runtime.workflow_worker` runs the loop; `run_worker_once` is the testable unit |
| Dispatcher guard | `workflow_ops.dispatch_queued_executions` | The in-process scheduler skips worker rows, so queue mode and the Phase 4 dispatcher never compete |
| Webhook lifecycle | `runtime/webhook_registry.py`, `api/v1/workflow_hooks.py` | Production `POST/GET /api/v1/hooks/<path>` only listens for active workflows; `POST/GET /api/v1/hooks-test/<path>` runs the newest saved definition, published or not; `webhook_urls()` returns both resolved URLs |
| Duplicate paths | `workflows.publish_workflow` | Publishing is refused with `WEBHOOK_PATH_CONFLICT` (409) when another active workflow in the organization already owns the path |
| Save execution data | `workflow_ops.save_execution_data_mode` | `all` (default), `none`, `on_error`: node items/inputs/outputs are stored only as the mode allows; statuses, durations, errors and `ExecutionEvent` rows are always kept |
| Crash journal | `runtime/crash_journal.py`, `runtime/lifecycle.py` | Each run is committed as `running` before the engine starts, tagged with the owning instance and a heartbeat. `mark_crashed` (startup + every scheduler tick) fails runs whose heartbeat stopped for `OPENAGENT_WORKFLOW_CRASH_TIMEOUT_SECONDS` (300 s) with `EXECUTION_CRASHED` and an `execution.crashed` event; `waiting` rows are left to the Wait/approval resumers |
| Metrics | `runtime/prometheus.py`, `api/metrics_endpoint.py` | `GET /metrics` (Prometheus text 0.0.4) renders the same `execution_metrics` snapshot — executions by status, queue depth/oldest, running, failure rate, duration p50/p95 — plus the in-process request metrics. Guarded by `OPENAGENT_METRICS_TOKEN` (Bearer or `?token=`); 404 when unset outside development |
| Graceful shutdown | `main.lifespan`, `runtime/lifecycle.py` | `begin_shutdown()` stops new runs (they persist as `queued`), then `wait_for_drain()` waits up to `OPENAGENT_WORKFLOW_SHUTDOWN_TIMEOUT_SECONDS` (25 s); anything unfinished stays `running` for the journal sweep |
| Chat tools | `koro.py` | `webhook_url` (production URL when active, test URL for a draft) and `operations_status` (running/queued/failure rate + queue mode) |

Tests: `tests/test_workflow_phase12_ops.py` (13) — queue persist + worker
completion + dispatcher guard, one-owner claim, shutdown queueing, drain
accounting, `none`/`on_error` data modes, crash marking with event, heartbeat
ownership, resolved webhook URLs, draft test URL vs production 404, duplicate
path detection, Prometheus rendering and the metrics token gate. The Alembic
head assertion in `test_workflow_version_schema.py` was updated to `029` (the
Phase 9 migration that was left untracked in the test).

Not verified here: no separate worker process was started against a live
PostgreSQL/Redis deployment, the scrape was not run by a real Prometheus, and
crash recovery was exercised by calling the sweep directly rather than by
killing a process. `/metrics` with a valid token was not exercised end to end
(the token gate is tested; the body is tested through the renderer).

---

## 10. Phase 13 — Chat control plane, 100% coverage

Goal: every operation in the product is one chat tool — UI is a view, not a
gate.

Structure:

* `apps/api/src/openagent/api/v1/koro.py` stays the router; split the tool
  implementations into `koro_tools/` modules by domain (`workflows`, `runs`,
  `nodes`, `credentials`, `agents`, `memory`, `mcp`, `subworkflows`,
  `operations`) so coverage is auditable.
* One `TOOL_SCHEMAS` table (name, JSON schema, permission, handler) used by
  (a) `_SYSTEM_PROMPT`, (b) the deterministic parser, (c) a new
  `GET /api/v1/koro/tools` endpoint, and (d) the docs page. No tool exists in
  only one of those.
* Plan → confirm → apply: destructive or broad edits return a diff preview;
  `data.preview` already exists, extend it to chat-level confirmation tokens.
* Undo: every mutating action returns `undo` metadata (previous version id);
  chat action `undo_last` restores it.

Coverage targets (tools that must exist by end of phase):

| Domain | Tools |
| --- | --- |
| Workflow | list, get, build, edit, rename, duplicate, delete, publish, unpublish, versions, restore, export/import |
| Nodes | add, update, remove, connect, disconnect, test, list available (tier/coverage), sub-workflow link |
| Settings | concurrency, timeout, error workflow, save-data mode, static data |
| Runs | list, get, node logs, retry, cancel, resume wait, queue status, debug/explain |
| Credentials | list (names only), create by reference, rotate, test, delete |
| Agents | list, create, update, run, stop, logs |
| Memory | search, set, delete |
| MCP | add server, test, list tools, remove |
| Marketplace/library | search templates, install, list installed |
| Operations | metrics summary, prune, webhook URL, health |

Acceptance: an automated test iterates `TOOL_SCHEMAS` and asserts each tool
resolves a handler, a permission and a prompt mention; a scripted conversation
builds, runs, debugs, publishes and deletes a workflow without any page visit.

### 10.1 Implemented (Phase 13 core)

| Piece | Where | Behaviour |
| --- | --- | --- |
| One tool table | `api/v1/koro_tools/registry.py` | 46 `ToolSpec`s: name, domain, kind (`read`/`write`/`destructive`), permission, JSON schema, confirmation/undo flags. `koro.ACTION_TYPES` is derived from it, so a name cannot exist in the router without being declared |
| Prompt | `koro._expanded_system_prompt()` | The `type` union in `_SYSTEM_PROMPT` is generated from the table (`<TOOL_ACTIONS>` placeholder); the model can only pick a declared tool |
| Endpoint | `GET /api/v1/organizations/{org}/copilot/tools` | The catalog, filterable by `domain` and `kind`, with `total`/`returned`/`domains`; permission `workflow:read` |
| Plan → confirm → apply | `koro_tools/confirm.py`, `_apply_action` gate | `delete`, `restore` and `prune` return `status="confirmation_required"`, a preview and a signed, 5-minute token carrying the exact action. Permission is checked **before** a token is issued. Neither a tampered token nor a token for another action can be replayed |
| Confirm from chat | `_confirmation_plan` | `confirm <token>` decodes the signed action and applies exactly that; no server-side pending-state table |
| Undo | `undo_last` + `returns_undo` flags | Mutating version-producing tools return `data.undo = {action: "undo_last", workflow_id}`; `undo_last` restores the previous version as a new draft (version history is the undo log) |
| New tools | `koro.py` | `export_workflow`, `import_workflow` (same validated create path), `node_logs` (per-node status/error/items of a run, filterable by node), `resume_wait`, `prune` (org-scoped), `health` |
| Org-scoped pruning | `workflow_ops.prune_executions` | Accepts `organization_id`, so a chat/API prune can never delete another tenant's history |

Tests: `tests/test_koro_tool_registry.py` (10) — table audit (domains,
kinds, permissions, schemas), prompt/registry equality, the endpoint and its
filters, token issue/expiry/tamper, delete without/with confirmation, chat
confirmation replay, undo restoring the previous version, undo metadata,
export/node logs/resume/prune/health, and the scripted lifecycle conversation
(build → run → diagnose → publish → delete with confirmation). `test_koro_tools.py`
updated for the two-step delete/restore flow (42 tests together).

Remaining Phase 13 gaps (not implemented in this pass): credential
create/rotate/test/delete, agent update/run/stop/logs, memory search/set/delete,
MCP add/test/list/remove, and marketplace search/install/list chat tools. Each
is a separate domain integration with its own service layer; the registry makes
adding them a single table entry plus a handler, and the audit test enforces
that both exist together.

Not verified here: no model-backed conversation was run (the scripted test uses
the deterministic parser); the web assistant renders the confirmation reply
as text — it does not yet offer a confirm button, so the token must be sent as a
message.

---

## 11. Phase 14 — UI collapse: 114 routes → 6 surfaces

Current inventory: 114 `page.tsx` routes, sidebar primary nav of four items
plus a "More" disclosure with 20+ links. Target:

| Surface | Route | Purpose |
| --- | --- | --- |
| Chat | `/assistant` | the whole product: build/edit/run/debug/settings/credentials/library |
| Workflows | `/workflows` | browse (list, search, status) |
| Canvas | `/workflows/[id]` | graph editing + run panel only |
| Runs | `/runs`, `/runs/[id]` | execution history + node-by-node logs |
| Settings | `/settings` (+ `/settings/*` admin subpages) | org, members, models, billing, secrets, enterprise |
| Auth/account | `/login`, `/register`, `/account` | unchanged |

Disposition of the remaining pages:

* **Becomes a chat tool, page removed** (redirect to `/assistant` with a
  pre-filled prompt): `/agents*`, `/orchestrations*`, `/management*`,
  `/memory`, `/mcp*`, `/integrations*`, `/tools`, `/browser*`, `/code*`,
  `/sandbox*`, `/templates*`, `/skills*`, `/presets`, `/marketplace*`,
  `/quality*`, `/approvals*`, `/cloud*`, `/operations*`, `/chat/[workflowId]`,
  `/docs`, `/developer/*`, `/publisher/*`, `/master/*`, `/platform`.
* **Admin-only, stays under `/settings`**: `/settings/*`, SSO/SCIM/MFA pages
  (already nested), `/master/billing`.
* **Keep as-is**: `/workflows*`, `/runs*`, `/assistant`, auth pages.

Mechanics:

1. Add `apps/web/src/app/(legacy)/...` redirect pages that map each removed
   route to `/assistant?prompt=<intent>` (or `/settings/...` for admin).
2. Trim `sidebar.tsx` to the six surfaces above; remove the "More" disclosure
   once the redirects land. Keep `Ctrl/Cmd+K` for navigation parity.
3. Add a chat "open page" tool so any remaining browse task is one message away.
4. Track parity with a test that asserts the route count and that removed
   routes redirect.

Acceptance: sidebar has ≤ 7 entries; no capability is lost (each removed page
has a documented chat tool or settings path); `npm run build` and typecheck pass;
the workflow canvas and run panel remain unchanged.

### 11.1 Implemented (Phase 14)

Shipped as one declarative map instead of per-page copies:

* `apps/web/src/features/navigation/surfaces.ts` is the single source: `PRIMARY_NAV`
  (five entries: Chat, Workflows, Executions, Settings, Account) and
  `LEGACY_REDIRECTS` (86 former routes → one intent string each), plus
  `legacyTarget(route)`, which returns `/assistant?prompt=<intent>` and throws
  for an undeclared route.
* Every removed page keeps its URL as a three-line `redirect(legacyTarget(…))`
  component, so bookmarks, docs links and the `?workflow=`-style deep links keep
  working and land in the chat with the right prompt. Dynamic segments
  (`/agents/[id]`, `/templates/[slug]`, `/chat/[workflowId]`, …) forward the
  same intent without needing the parameter.
* 28 routes keep real pages: the five auth screens, `/account`, `/assistant`,
  `/workflows` (+ `new`, `[id]`, `[id]/edit`), `/runs` (+ `[id]`), the eleven
  `/settings/*` pages including `billing` and `enterprise`, and `/master/billing`.
  `/` itself forwards to the assistant.
* `sidebar.tsx` renders `PRIMARY_NAV` only: the "More" disclosure, the
  developer block, the platform block and the marketplace feature flag are gone,
  along with their 40+ links.
* New chat tool `open_page` (`page` ∈ assistant/workflows/runs/settings/account/
  workflow/execution, optional `workflow_id`/`execution_id`) resolves a validated
  path and returns `data.path` + `data.label`. `AssistantActionCard` renders an
  "Open …" link from that payload, so "open the run that failed" ends in one
  click instead of a hunt through the sidebar.
* `features/navigation/surfaces.test.ts` is the parity guard: it walks
  `src/app` for `page.tsx` files and asserts (a) 114 pages, (b) exactly the 28
  kept routes own real pages, (c) every other route has a `LEGACY_REDIRECTS`
  entry and a page that calls `legacyTarget` for it, (d) no redirect entry points
  at a deleted page, (e) the intent is URL-encoded into the assistant prompt,
  (f) `PRIMARY_NAV` has ≤ 7 unique entries and the sidebar links no legacy route.

Capability mapping for removed pages: chat tools cover agents, memory, MCP,
credentials, marketplace, tools, templates and skills (Phase 13, extensions
listed there); settings pages remain at `/settings/*`; the workflow canvas
(`/workflows/[id]`) and the run panel (`/runs/[id]`) are untouched.

Verified: `npm run typecheck`, `npx vitest run src/features/navigation
src/features/workflows src/features/koro` (136 passed), `npm run build`
(every forwarding route prerenders at 343 B), backend `ruff` clean and
`pytest tests/test_koro_tool_registry.py tests/test_koro_tools.py` (43 passed).
Not verified: the redirects in a live browser (only build-time prerender and the
route test), and the assistant's action-card link rendering was typechecked, not
clicked.

---

## 12. Sequencing and effort

| Phase | Scope | Depends on | Effort |
| --- | --- | --- | --- |
| 7 | Node retry / onError / executeOnce / disabled / per-node timeout | — | **done** |
| 8 | Binary data model, object store, file nodes | 7 | **done** |
| 9 | Sub-workflows, static data, execution tree | 7 | **done** |
| 10 | Sandboxed Code node (Python, then JS) | 8 | **done** |
| 11 | Node tiers A/B/C + coverage endpoint | 7–10 | **done** |
| 12 | Queue mode, webhook lifecycle, crash journal, metrics, save modes | 9 | **done** |
| 13 | Chat tool registry, full coverage, undo, confirmations | 7–12 (each tool ships with its phase) | **core done** |
| 14 | UI collapse to six surfaces | 13 | **done** |

Order rationale: 7 unlocks 8–11 (every executor uses the same node contract);
13 is continuous, ending with a formal audit; 14 is last so nothing is removed
before its chat replacement exists.

---

## 13. Risks and decisions

| Risk | Decision |
| --- | --- |
| n8n license (Sustainable Use License) | Clean-room behavior ports only; no copied source; node/parameter names are factual API surface |
| 300+ nodes is unbounded | Tiering + generic HTTP/MCP/extension import; the coverage endpoint states the truth |
| Queue mode changes failure modes | Feature-flagged, worker reuses `run_workflow`, tests run both in-process and queued |
| Removing 100+ pages breaks users | Redirect-with-intent first, sidebar trim second, deletion last; each removed page must have a chat tool |
| Sandboxed code is a security boundary | Reuse the existing Docker sandbox with no network, read-only rootfs, CPU/memory/time limits, output cap |
| n8n keeps moving | Pin the reference commit in this doc; refresh deliberately when a phase starts |

---

## 14. Verification

```bash
# API: node semantics, binary data, sub-workflows, code sandbox
cd apps/api && SECRET_KEY=$(printf 'a%.0s' {1..40}) ENCRYPTION_KEY=$(printf 'b%.0s' {1..40}) \
  DATABASE_URL="sqlite+aiosqlite://" ./.venv/Scripts/python.exe -m pytest tests -q -p no:warnings

# Code-node runner only (fast); needs node on PATH for the JavaScript case
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_workflow_code.py -q

# touched-file lint
cd apps/api && ./.venv/Scripts/python.exe -m ruff check src/openagent/runtime tests/test_workflow_*.py

# web: catalog/registry/validation and typecheck
cd apps/web && npm run typecheck && npx vitest run src/features/workflows
```

Each phase updates this file's gap matrix (◐/✗ → ✅) and records what was not
verified (browser UI, PostgreSQL profile, live providers, queue mode) in the
same place.
