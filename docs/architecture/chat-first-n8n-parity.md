# Chat-first automation platform — n8n parity blueprint

Status: **active plan**. Phase 1 (expression engine + item model), Phase 2
(item execution contract, expression conditions, waits, webhook resume, merge
fan-in), Phase 3 (error workflow, pinned previews, partial execution, per-node
item metadata), Phase 4 (per-workflow concurrency with queued dispatch,
retention pruning, webhook limits, execution budgets and `/operations`
metrics), the registry-driven node catalogue from Phase 5 (now 59 nodes, 6
triggers after the Phase 8 file/binary nodes, including the core n8n
data/logic ports) and the chat
surfaces from Phase 6 (`/assistant`, the global Koro dock, the builder panel
and workflow chat) are implemented in this repository. The one Phase 4 item
still open is routing workflow runs through the Redis worker (queue mode);
the queued dispatcher is the seam for it.

The audited continuation — node-level retry/error semantics, binary data,
sub-workflows, the sandboxed Code node, node tiers, queue mode, full chat tool
coverage and the UI collapse from 114 routes to six surfaces — lives in
[`n8n-parity-roadmap.md`](./n8n-parity-roadmap.md).

---

## সংক্ষিপ্ত সারাংশ (Bangla)

লক্ষ্য: Open Agent-কে n8n-এর মতো শক্তিশালী অটোমেশন ইঞ্জিন দেওয়া, কিন্তু UI-তে
অপশনের ভিড় না রেখে **একটাই শক্তিশালী চ্যাট বক্স** (প্রম্পট + ভয়েস) থেকে সব
কাজ — ওয়ার্কফ্লো তৈরি, এডিট, রান, ডিবাগ, পাবলিশ, ক্রেডেনশিয়াল, মেমোরি —
চালানো।

n8n-এর কোর ইঞ্জিন (`reference/n8n`, sparse clone) পড়ে যে চারটি ভিত্তি দরকার
পাওয়া গেছে:

1. **আইটেম ডেটা মডেল** — প্রতিটি নোড `{json, binary, pairedItem}` আইটেমের
   লিস্ট নেয়/দেয়, `pairedItem` দিয়ে আইটেম-ট্রেসিং হয়।
2. **এক্সপ্রেশন ইভ্যালুয়েটর** — `{{ ... }}`-এর ভিতরে JS-সদৃশ ভাষা:
   `$json`, `$node`, `$('Node')`, `$items()`, `$input`, `$vars`, `$now`,
   `$today`, `$workflow`, `$execution`, `$parameter`, `$binary`, মেথড কল,
   অ্যারিথমেটিক, টার্নারি, অপশনাল চেইনিং।
3. **প্রোডাকশন অপারেশনস** — ওয়েবহুক/অ্যাক্টিভেশন, রিট্রাই/টাইমআউট,
   error workflow, কনকারেন্সি লিমিট, wait/resume, এক্সিকিউশন প্রুনিং,
   কিউ মোড, ভার্সনিং ও পিনড ডেটা।
4. **চ্যাট কন্ট্রোল প্লেন** — একটাই ইন্টেন্ট রাউটার + টুল রেজিস্ট্রি, যার
   মাধ্যমে চ্যাট থেকে উপরের সব কিছু করা যায়; UI শুধু ক্যানভাস + রান প্যানেল।

এই ডকুমেন্টে প্রতিটি ধাপের ফাইল-লেভেল প্ল্যান, অ্যাকসেপ্টেন্স ক্রাইটেরিয়া
ও বর্তমান গ্যাপ টেবিল আছে। Phase 1 এখনই কোডে আছে এবং টেস্টেড।

---

## 1. What was studied in n8n

The reference checkout lives at `reference/n8n` (sparse, blob-filtered clone of
`n8n-io/n8n@master`, `packages/workflow` + `packages/core` + the relevant
execution-engine and node sources). It is ignored by git.

| Concern | n8n source |
| --- | --- |
| Expression grammar (closed AST) | `packages/workflow/src/expressions/native-evaluation/grammar.ts` |
| Expression parser | `.../native-evaluation/parser.ts` |
| Expression evaluator + limits | `.../native-evaluation/evaluator.ts` (MAX_DEPTH 64, MAX_STEPS 100k, MAX_RESULT_LENGTH 1 MB) |
| Legacy expression engine / tournament | `packages/workflow/src/expression.ts` |
| Data proxy (`$json`, `$node`, `$items`, `$input`, `$now`, …) | `packages/workflow/src/workflow-data-proxy.ts` |
| Item types (`INodeExecutionData`, `pairedItem`) | `packages/workflow/src/interfaces.ts` |
| Execution loop, branch resolution, partial execution | `packages/core/src/execution-engine/workflow-execute.ts` |
| Node context / `this.helpers` | `packages/core/src/execution-engine/node-execution-context/` |
| Built-in node contracts (Set, Code, If, Merge, SplitInBatches, HTTP) | `packages/nodes-base/nodes/…` |

Two findings shape this plan:

* n8n keeps two expression paths — a fast **native, closed-grammar interpreter**
  and the older JS sandbox, with a "fall back to the engine" escape hatch for
  anything the fast path cannot represent. The allowlists (string/number/array
  methods, iterator callbacks, time getters) are what make it safe. Our Python
  port mirrors that split: native grammar first, legacy path resolver second.
* n8n's item model is the single most load-bearing contract in the product.
  Everything else (expressions, partial execution, pinned data, NDV previews,
  debug) reads from it.

---

## 2. Gap analysis — Open Agent vs n8n

| Capability | n8n | Open Agent today | Plan |
| --- | --- | --- | --- |
| Item envelope `{json, binary, pairedItem}` | Core contract | Plain JSON values; chat trigger uses a partial envelope | **Phase 1 (done, read path)**; Phase 2 (write path) |
| `{{ }}` expression language | JS subset: `$json`, `$node`, `$()`, `$items()`, `$input`, `$vars`, `$now`, `$today`, `$workflow`, `$execution`, `$parameter`, `$binary`, methods, iterators, ternary, optional chaining | Path resolver: `nodes.x.y`, `variables.x`, `trigger.x`, `env.X` | **Phase 1 (done)** |
| Expressions in conditions | Same grammar | Custom `contains / starts with / and / or` parser | Phase 2 (route conditions through grammar) |
| Run modes | Run once per item / once for all items, per-node | Whole-node execution | Phase 2 |
| Partial execution / "execute node" | Yes, with dirty-node detection | `POST /workflows/{id}/execute-node` rebuilds upstream from pins/`upstream` | **Phase 3 (done)** |
| Pinned data | Per-node pin | Pinned output on the definition feeds previews/step tests only | **Phase 3 (done)** |
| Error workflow (workflow-level "On error") | Yes | `settings.error_workflow_id` run with `{execution, workflow, error, lastNodeExecuted}` | **Phase 3 (done)** |
| Concurrency limits per workflow | Yes | `settings.max_concurrency` + queued dispatch | **Phase 4 (done)** |
| Execution pruning / retention | Settings-driven | `prune_executions()` on the scheduler tick | **Phase 4 (done)** |
| Queue mode / workers | Redis queue, main + worker | Redis workers exist (`apps/worker`) | Phase 4 (wire workflow runs) |
| Wait / resume | Wait node + form/webhook resume | Timed waits + `wait_for_webhook` resume route | **Phase 2 (done)** |
| Versioning | Workflow history, restore | Versioned definitions | Already present |
| Node library breadth | 400+ nodes | 51 manifest nodes + 6 triggers (file/binary nodes in Phase 8, Execute Workflow trigger in Phase 9); provider nodes skip locally | **Phase 5 (registry + core ports done)** |
| Chat control plane | n8n Assistant (build/edit/debug) | Koro (build/edit/run/publish/diagnose) + Assistant page + voice | Phase 6 (full command coverage) |
| UI surface | Canvas-heavy | Chat + workflows + runs primary, everything else folded | Phase 6 (single chat box) |

---

## 3. Phase 1 — Expression engine and item model (implemented)

### 3.1 Files

| File | Role |
| --- | --- |
| `apps/api/src/openagent/runtime/expressions.py` | n8n closed-grammar port: tokenizer, Pratt parser, evaluator, data proxy, `N8nDateTime`, JS coercion helpers |
| `apps/api/src/openagent/runtime/items.py` | `{json, binary, pairedItem}` helpers: `as_items`, `make_item`, `item_json`, `first_json`, `last_json`, `map_items`, `paired_item_index` |
| `apps/api/src/openagent/runtime/local_engine.py` | `expression_data(ctx)` builds the proxy from a `RunContext`; `resolve()` tries the n8n grammar and falls back to the legacy resolver |
| `apps/api/tests/test_expressions.py` | 16 unit tests covering roots, methods, iterators, time, coercion, templates, items, engine integration |

### 3.2 Supported expression surface

```
{{ $json.customer.name }}                    member + nested
{{ $json.tags[0] }}                          index
{{ $json.opt?.value }}                       optional chaining
{{ $("Fetch").first().json.id }}             node reference proxy
{{ $items("Fetch").length }}                 item list
{{ $input.all().map(i => i.json.total) }}    iterators with arrow callback
{{ $node["Webhook"].json.body.id }}          legacy node reference
{{ $vars.token }}  {{ $env.HOME }}           variables / environment
{{ $workflow.name }} {{ $execution.id }}     workflow + run metadata
{{ $parameter.limit }} {{ $binary.file.fileName }}
{{ $now.plus(1, "days").toISODate() }}       Luxon-shaped time helpers
{{ $itemIndex }} {{ $runIndex }}
{{ $json.total > 10 ? "big" : "small" }}     ternary
{{ 1 + 2 * 3 }} {{ "a" + 1 }}                JS arithmetic / concatenation
```

String methods: `toUpperCase toLowerCase trim trimStart trimEnd includes
startsWith endsWith slice substring split indexOf charAt replace replaceAll
concat padStart padEnd repeat isEmpty isNotEmpty toNumber toBoolean toJson`.
Number: `toFixed toPrecision toString`. Array: `includes indexOf lastIndexOf
join slice at concat flat toSorted toReversed length`, iterators `map filter
find some every`. Object: `keys values toJsonString hasField isEmpty
isNotEmpty merge`. Date: getters `year month day hour minute second
millisecond weekday weekNumber quarter daysInMonth offset`, methods
`toMillis toSeconds toUnixInteger toISO toISODate toISOTime toString plus
minus format`.

Safety properties (same as n8n's native path):

* no Python `eval`, no attribute access outside the allowlists;
* `MAX_DEPTH`, `MAX_STEPS`, `MAX_RESULT_LENGTH` budgets;
* unknown roots raise `UnknownReferenceError`, which the engine uses to fall
  back to the legacy resolver — so every previously stored workflow still runs;
* an AST cache (512 entries) keeps the parser off the hot path.

### 3.3 Item model

`as_items()` normalises anything a node emitted into item envelopes:

```python
as_items({"body": {...}})            -> [{"json": {"body": {...}}}]
as_items([{"a": 1}, {"a": 2}])       -> [{"json": {"a": 1}, "pairedItem": {"item": 0}}, ...]
as_items({"items": [{"a": 1}]})      -> [{"json": {"a": 1}, "pairedItem": {"item": 0}}]
as_items(make_item({"a": 1}, 0))     -> unchanged
```

`$json` reads the current item's payload, `$input` the items handed to the
node, and `$node`/`$()` read upstream node output through `as_items`, so
expressions work the same whether a stored workflow emits raw JSON or
envelopes.

### 3.4 Acceptance criteria (met)

* `pytest tests/test_expressions.py` — 16 passed.
* Existing workflow suites — 99 passed (`test_workflow_chat`,
  `test_workflow_definition`, `test_connectors`); subworkflow tests need a
  PostgreSQL instance and error out at connect in the SQLite dev profile.
* No workflow definition or API contract changed.

---

## 4. Phase 2 — Item model as the execution contract (implemented)

Goal: node output is an item list, `$json` is the node's input item, and a long
wait parks the run instead of being truncated.

### 4.1 What shipped

1. **Run mode** — every node accepts `run_mode: "all" | "each"` (validated in
   `workflow_definition.py` as `RUN_MODES`). The default is `all`, so stored
   workflows keep their one-pass semantics; `each` runs the node body once per
   incoming item with `$json`, `$itemIndex` and `$input` bound to that item and
   collects the results into an item list with `pairedItem`.
2. **Items in every node run** — `NodeOutcome.items` exists; list-producing
   nodes (filter, map, sort, limit, remove_duplicates, split_out) set it with
   `pairedItem` relative to their own input, and every other node records one
   item. `node_runs[i]["items"]` is additive; the legacy `outputs` view is
   unchanged for the builder, Koro and the API.
3. **n8n-shaped execution data** — `execution.metadata["execution_data"]`
   stores `{"nodes": {nodeId: [{json, pairedItem}]}}`, ready for the NDV and
   run panel without breaking the existing `node_runs` consumers.
4. **`$json` semantics** — a node's expressions read its first incoming item
   (`_execute_with_policy` binds a child context), while the legacy `json` root
   keeps resolving the trigger payload so stored workflows behave the same.
   `$input.all()` sees every incoming item (`RunContext.input_items`).
5. **Conditions** — `evaluate()` tries the n8n grammar first
   (`{{ $json.total > 10 }}`, `{{ !$json.done }}`, `===`) and falls back to the
   legacy operator words. Edges also honour `condition.when`
   (`always`/`success`/`failure`) and `condition.expression`.
6. **Waits** — a Delay longer than `MAX_SYNC_DELAY_SECONDS` returns a `waiting`
   outcome with `resume_at`; `run_workflow` persists the run as `WAITING` with
   `metadata.wait` holding the definition, trigger, variables, outputs and
   completed runs (the wait node itself stored as completed).
   `schedule_runner.resume_due_waits()` sweeps due rows on the scheduler tick
   and `run_workflow(..., resume_from=execution)` continues the same execution
   row, skipping completed nodes and re-emitting no duplicate events.

### 4.2 Semantics worth knowing

* `pairedItem` is relative to the node's own input (n8n chains the same way),
  not the graph's first node.
* `{"items": [...]}` is treated as an item list only where the engine emits it
  (list nodes); a Set/Transform payload with an `items` key stays one item.
* A parked run is resumed by time only; approvals continue to use the existing
  human resume route.

### 4.3 Acceptance (met)

`apps/api/tests/test_workflow_items.py` (8 tests) covers per-item mode,
paired items through sort/limit/filter, edge conditions, long waits, validation
and a full persist → sweep → resume cycle against SQLite. The workflow
regression suites (expressions, chat, definition, subworkflow, connectors, Koro
history) run 137 tests green.

### 4.4 Finished remainder of this phase

* **Merge fan-in** — `merge` consumes every inbound branch (edge labels can no
  longer collide) and emits an item list: `mode: "append"` (default),
  `by_index` (positional combine) or `by_key` (match on `config.field`).
  `first` keeps the first non-empty branch. The legacy `merged` object is still
  in `outputs` for older consumers.
* **Run mode in the inspector** — node settings → *Run mode* writes
  `run_mode: "all" | "each"` (undoable, validated by the API).
* **Webhook-driven resume** — a delay with `config.wait_for_webhook` (or
  `mode: "webhook"`) parks as `WAITING_FOR_WEBHOOK`; `metadata.wait` carries a
  `webhook_token` and `POST /organizations/{org}/workflows/{id}/executions/{id}/webhook-resume`
  resumes the same row with the caller's payload (`{{ trigger.webhook.* }}`).
  The token is constant-time compared; timed waits keep using the sweep.
* **Execution-data pruning** — stored `items` (both `node_runs` and
  `execution_data`) are capped at `MAX_PERSISTED_ITEMS_PER_NODE` (100) with
  `items_truncated` per node and `summary.items_pruned`; the API response still
  returns the complete run.

---

## 5. Phase 3 — Production execution semantics (implemented)

1. **Error workflow** — `settings.error_workflow_id` (validated in
   `workflow_definition.py`, typed in `workflow_schema.py`, editable in the
   workflow settings panel) is read by `run_workflow()` after a failed run.
   `_run_error_workflow()` loads the handler inside the same organization and
   runs it with `{execution, workflow, error, lastNodeExecuted}`; a missing or
   malformed id is recorded in `metadata.error_workflow` instead of breaking
   the run, a self-reference is skipped, and the handler itself is invoked with
   `trigger_error_workflow=False` so failures cannot loop.
2. **Pinned data in previews** — `run_single_node(use_pinned=True)` (the
   default for `POST .../execute-node`) exposes `settings.pinned_data` both to
   expressions (`{{nodes.x.y}}`, `$node`) and as the target node's input items;
   real runs never pass the flag, so pins cannot leak into production data.
3. **Partial execution** — the preview also resolves the target node's inbound
   edges from pinned output or from a caller-supplied `upstream` map, so
   "execute this node" works on a node whose upstream has not run in this
   session; `sources` lists which nodes supplied data.
4. **Node-level execution metadata** — every run records `input_items`,
   `output_items`, start/end, duration, attempt, `ports`, `reason` and status.

Acceptance: a failing node triggers the configured error workflow once;
pinned data shows in previews but is excluded from real runs; "execute node"
works on a node whose upstream has not run in this session.
`apps/api/tests/test_workflow_production.py` covers merge modes, webhook wait +
resume, pruning, error workflow (including the self-reference guard) and
pinned/partial preview execution.

---

## 6. Phase 4 — Operations hardening (implemented, except queue mode)

`apps/api/src/openagent/runtime/workflow_ops.py` holds the policy helpers;
`local_engine.run_workflow` and the scheduler tick call them.

1. **Per-workflow concurrency** — `settings.max_concurrency` (1–32, already
   validated) is the slot count. `run_workflow` counts `running` executions
   for the workflow and, when the limit is reached, persists the run as
   `queued` (with its definition, trigger input and version) instead of
   dropping it. `dispatch_queued_executions()` runs queued rows FIFO on every
   scheduler tick once a slot frees up. No new table: the workflow settings
   field is the source of truth, like n8n's workflow "Concurrency" setting.
   Without the setting runs start immediately (backwards compatible).
2. **Execution pruning** — `prune_executions()` deletes terminal executions
   older than `OPENAGENT_WORKFLOW_RETENTION_SECONDS` (default 90 days, the
   `enterprise.py` platform floor; `0` disables), keeping the newest
   `OPENAGENT_WORKFLOW_KEEP_LAST` (default 50) runs per workflow. Their
   execution events go with them. The scheduler runs the sweep hourly.
3. **Rate limits & timeouts** — webhooks enforce a per-organization sliding
   one-minute budget (`OPENAGENT_WEBHOOK_RATE_LIMIT`, default 120; 429 +
   `Retry-After`) and a request-body cap (`OPENAGENT_WEBHOOK_MAX_BODY_BYTES`,
   default 1 MiB; 413). `settings.execution_timeout_seconds` is enforced as an
   `asyncio.wait_for` budget; an over-budget run fails with
   `EXECUTION_TIMEOUT` instead of running forever.
4. **Health/observability** — `GET /api/v1/operations/workflows` returns run
   totals by status, queue depth and oldest queued age, failure rate and
   p50/p95/avg duration for the window (24 h by default).
5. **Queue mode for workflow runs** — *not built.* Agent runs still use the
   Redis worker (`apps/worker`); workflow runs stay in the in-process engine.
   The queued-execution dispatcher above is the seam a future worker-backed
   runner would consume.

Acceptance: over-limit runs are queued and started later (never dropped);
pruning keeps the configured window and the newest N; an over-budget run fails
with `EXECUTION_TIMEOUT`; webhook flood/oversized payloads are rejected.
`apps/api/tests/test_workflow_operations.py` covers all of these.

---

## 7. Phase 5 — Node breadth (registry + core ports implemented)

Node categories come from a registry, not hand-written menus:

1. `apps/api/src/openagent/registry/` — generated from the authoring catalog
   (`apps/web/src/features/workflows/node-catalog.ts`) into
   `node-registry.json`; the API loads it without Node.js and a web test keeps
   both copies in sync. Each entry carries `type`, `version`, ports, config
   fields, defaults, category and docs.
2. Generate: the builder palette, the `GET /nodes/registry` API, the Koro
   prompt catalogue and the backend/frontend validation rules all read the
   same document.
3. **Core n8n ports** — Set, If (condition), Switch, Merge, Split In Batches,
   Code, HTTP Request (`webhook`), Wait (`delay`), Schedule/Webhook triggers,
   Respond to Webhook, Edit Fields, Remove Duplicates, Sort, Limit and
   Aggregate all exist as manifests plus local executors. The Phase 5 additions
   are `edit_fields`, `aggregate` (collect/count/sum/average/group_by),
   `split_in_batches`, `code` and `respond_to_webhook`; the responder node
   drives the real HTTP response of `/hooks/<path>` (status, body, headers).
4. **Scope of the local executors.** AI/tool/connector/coding nodes are
   provider-backed: they are recorded as `skipped` with a reason rather than
   faked. The `code` node evaluates expressions only — no arbitrary JS/Python
   runs in the local engine; anything needing a sandbox uses the existing code
   workspace nodes.
5. Community nodes: the existing `.oaext` extension system already signs and
   versions packages — a node extension type is the n8n "community node"
   equivalent.

Acceptance: adding a node type is one manifest entry plus one executor; the
palette, API registry, chat catalogue and both validators pick it up with no
other edits. `apps/api/tests/test_workflow_nodes.py` covers the five nodes and
`apps/web` keeps the registry and validation in sync.

---

## 8. Phase 6 — One chat box, everything else on demand

The sidebar is already chat-first (`Chat`, `Workflows`, `Executions`,
`Settings`; everything else sits behind one disclosure). The remaining work is
making the chat actually *capable*, not just present.

### 8.1 Control plane

```
User message / voice transcript
        │
        ▼
intent router  (apps/api/src/openagent/api/v1/koro.py::_command_plan)
        │  deterministic commands first: list / run / publish / unpublish /
        │  summarize / diagnose / repair / create agent / explain
        ▼
tool registry  (one JSON-schema tool per platform action)
        │  workflows.create / update / delete / activate / deactivate
        │  executions.list / get / debug / retry
        │  nodes.add / update / remove / connect / test
        │  credentials.list / create (value never leaves the vault)
        │  memory.search / set, agents.create / run, mcp.add / test
        ▼
permission check → action → read-only context back to the model
```

Rules: the model only ever sees names, ids, statuses and redacted field
summaries — never definitions, parameter values or credentials; every mutating
action is server-side, organisation-scoped and re-checked against the caller's
permissions; every applied action returns a link (`workflow`, `run`, `agent`)
and is undoable on the canvas.

### 8.2 Surfaces

* **/assistant** — the full-page chat (history rail, saved conversations,
  per-user state). This is the primary product surface.
* **Global dock** (`Ctrl/Cmd + J`) — same conversation on any route, carrying
  the current route context (workflow outline, validation state, last run).
* **Builder side panel** — graph edits happen here so they stay undoable.
* **Voice** — `useSpeechInput.ts` already transcribes; wire a push-to-talk
  button in both composers and keep the transcript editable before send.
* **Debug loop** — a failed step exposes "Ask Koro to fix", which reads the
  error, code and payloads (redacted) and proposes a patch.

### 8.3 De-cluttering rules

1. Any setting that only configures a workflow belongs in the workflow's
   inspector or the chat — never a top-level page.
2. A page stays only if it has a browse task that chat cannot do well
   (workflow list, execution history, marketplace, settings).
3. New features ship with a chat action first and a UI control second.

### 8.4 Implemented actions (Phase 6)

`KoroActionResponse.type` lists every typed tool the chat can return. Beyond the
lifecycle tools (list, build, edit, run, publish/unpublish, delete, duplicate,
versions, restore, runs, retry, cancel, explain, diagnose, schedule, variables,
agents, credentials), Phase 6 adds:

| Action | Fields | Permission | Effect |
| --- | --- | --- | --- |
| `test_node` | `node` (name or id), optional `input` | `workflow:execute` | Runs one step through `run_single_node` on the latest saved version and returns status, item count and a bounded output preview |
| `update_workflow` | `name`, `description` | `workflow:update` | Renames the workflow (re-slugs, keeps slugs unique per org) and/or updates the description, with an audit entry |
| `set_workflow_setting` | `max_concurrency` (0 clears), `execution_timeout_seconds` (1–86400), `error_workflow_id` or `error_workflow` name, `clear_error_workflow` | `workflow:update` | Mutates `definition.settings`, validates and saves a new version — the same settings Phase 4 reads at runtime |

Deterministic phrases (no model needed): "test the Send notification node",
"rename the workflow to Intake v2", "set concurrency to 2", "set a timeout of
5 minutes", "when this fails run Failure handler", plus "wait 10 minutes"
read when adding a delay step. Agent *runs* stay out of the chat: they need the
Redis worker queue, which the local SQLite profile does not run.

Phases 7–13 grow the same table to 46 tools without adding a second surface:
node semantics (`set_node_settings`), sub-workflows and static data
(`link_subworkflow`, `set_static_data`), capability honesty (`node_coverage`),
production operations (`node_logs`, `resume_wait`, `prune`, `health`,
`operations_status`, `webhook_url`), portability (`export_workflow`,
`import_workflow`), safe mutation (`undo_last`, signed plan → confirm → apply
for `delete`/`restore`/`prune`) and navigation (`open_page`). The registry is
also the prompt's action union and the `GET …/copilot/tools` catalogue, so the
chat, the model and the docs cannot drift apart.

Acceptance: from a single conversation a user can create, edit, run, debug,
publish, unpublish and delete a workflow, manage its credentials by reference,
and inspect runs — without opening another page. The only graph edits that
require the canvas are free-hand node positioning.
`apps/api/tests/test_koro_tools.py` covers the new actions end to end.

---

## 9. Order of work and effort

| Phase | Scope | Effort |
| --- | --- | --- |
| 1 | Expression engine + item read model | **done** |
| 2 | Items as the execution contract, conditions, waits, merge, pruning | **done** |
| 3 | Error workflow, pinned data in previews, partial execution | **done** |
| 4 | Concurrency, pruning, limits, observability (queue mode open) | **done** (except queue mode) |
| 5 | Manifest-driven node registry + core node ports | **done** (59 nodes, 6 triggers) |
| 6 | Tool registry, voice, chat actions for every resource | **done** (agent runs need the Redis queue) |

Phases 2–3 are the ones that unblock everything else; node breadth (Phase 5) is
parallelisable and should not start before the item contract is stable.

---

## 10. Risks and decisions

* **Two expression paths.** Keeping the legacy resolver costs a fallback on
  every unknown root. It is deliberate: stored workflows must not break. Remove
  it only after a migration pass over saved definitions.
* **Zod/pydantic parity.** n8n validates workflow JSON with zod; here the
  pydantic schema in `workflow_definition.py` is the contract. Any new field
  (`run_mode`, `settings.error_workflow_id`) must be added there first.
* **JS semantics in Python.** The port covers the documented surface, not every
  edge of the JS spec (regex arguments, prototype chains, `Intl`). Anything
  outside the surface raises instead of guessing.
* **Security.** Expressions can read organisation data only through the proxy
  the engine builds; no filesystem, network or module access exists in the
  grammar.

---

## 11. Verifying the work

```bash
# expression + item unit tests
cd apps/api
SECRET_KEY=$(printf 'a%.0s' {1..40}) ENCRYPTION_KEY=$(printf 'b%.0s' {1..40}) \
  ./.venv/Scripts/python.exe -m pytest tests/test_expressions.py -q

# workflow regression suites (no PostgreSQL needed)
SECRET_KEY=... ENCRYPTION_KEY=... \
  ./.venv/Scripts/python.exe -m pytest \
    tests/test_workflow_chat.py tests/test_workflow_definition.py \
    tests/test_connectors.py -q

# execution contract + production semantics + operations
SECRET_KEY=... ENCRYPTION_KEY=... DATABASE_URL="sqlite+aiosqlite://" \
  ./.venv/Scripts/python.exe -m pytest \
    tests/test_workflow_items.py tests/test_workflow_production.py \
    tests/test_workflow_operations.py -q
```

`reference/n8n` is a sparse checkout kept for reading only; it is excluded from
version control and is not built or imported by any package.
