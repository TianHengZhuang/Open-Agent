# Chat-first automation platform — n8n parity blueprint

Status: **active plan**. Phase 1 (expression engine + item model), Phase 2
(item execution contract, expression conditions, scheduled waits), the
registry-driven node catalogue from Phase 5 and the chat surfaces from Phase 6
(`/assistant`, the global Koro dock, the builder panel and workflow chat) are
implemented in this repository. Production execution semantics (Phase 3) and
operations hardening (Phase 4), including error workflows and per-workflow
concurrency, remain specified but not built yet.

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
| Partial execution / "execute node" | Yes, with dirty-node detection | `POST /workflows/{id}/execute-node` | Phase 3 (paired-item aware) |
| Pinned data | Per-node pin | Pinned output already stored on the definition | Phase 3 (feed `$json` from pins in preview) |
| Error workflow (workflow-level "On error") | Yes | Per-node `on_error: continue` only | Phase 3 |
| Concurrency limits per workflow | Yes | Worker-level only (`cloud` config) | Phase 4 |
| Execution pruning / retention | Settings-driven | Enterprise retention constants only | Phase 4 |
| Queue mode / workers | Redis queue, main + worker | Redis workers exist (`apps/worker`) | Phase 4 (wire workflow runs) |
| Wait / resume | Wait node + form/webhook resume | Approval parks run `waiting`, resume route exists | Phase 2 (delays > 10 s, webhook resume) |
| Versioning | Workflow history, restore | Versioned definitions | Already present |
| Node library breadth | 400+ nodes | ~30 local types + provider nodes | Phase 5 (registry-generated) |
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

### 4.4 Still open from this phase

* a per-item `merge` (fan-in of item lists) and a visual run-mode toggle in the
  inspector;
* webhook-driven resume in addition to the scheduler sweep;
* pruning execution data for very large item counts.

---

## 5. Phase 3 — Production execution semantics

1. **Error workflow** — add `settings.error_workflow_id` to the definition
   schema; on a failed run, `run_workflow()` launches that workflow with
   `{execution, workflow, error, lastNodeExecuted}` as trigger payload. Mirrors
   n8n's workflow settings panel; surfaced from chat as
   "when this fails, run X".
2. **Pinned data in previews** — pinned node output already lives on the
   definition; make `$json` and the NDV read pins when `mode=preview`, and
   never during a real run.
3. **Partial execution** — `execute-node` already exists; extend it to rebuild
   upstream items from pinned/stored data so "execute this node" works
   mid-graph, and add n8n-style dirty-node warnings.
4. **Node-level execution metadata** — start/end time, item counts per output
   port, `error` per item, `executionStatus` per node (already partially
   stored).

Acceptance: a failing HTTP node triggers the configured error workflow once;
pinned data shows in previews but is excluded from real runs; "execute node"
works on a node whose upstream has not run in this session.

---

## 6. Phase 4 — Operations hardening

1. **Per-workflow concurrency** — a `workflow_concurrency` table
   (`organization_id`, `workflow_id`, `limit`) checked before dispatch; runs
   over the limit are queued, not dropped. n8n's equivalent is the workflow
   settings "Concurrency" field.
2. **Execution pruning** — a scheduled job honouring `retention_seconds`
   from `apps/api/src/openagent/api/v1/enterprise.py`; soft-delete executions
   and their events, keep the last N per workflow.
3. **Queue mode for workflow runs** — route heavy nodes (AI, HTTP batches,
   code) through `apps/worker` with the existing Redis queue; the local
   in-process engine stays the default for SQLite dev.
4. **Rate limits & timeouts** — per-organization webhook rate limit, request
   body caps, and a global execution-time budget.
5. **Health/observability** — structured execution metrics (runs, failures,
   p95 duration, queue depth) exposed on `/operations`.

Acceptance: 10 parallel triggers with a limit of 2 never exceed 2 concurrent
runs; pruning keeps the configured window; killing a worker leaves a
recoverable `running` execution.

---

## 7. Phase 5 — Node breadth without UI sprawl

Node categories come from a registry, not hand-written menus:

1. `apps/api/src/openagent/registry/` — one manifest per node type
   (`type`, `version`, `group`, `properties`, `outputs`, `credentials`,
   `run_mode`, expression-compatible parameter list).
2. Generate: (a) the local executor mapping, (b) the builder's node palette,
   (c) the chat tool catalogue the assistant uses to add nodes by name,
   (d) validation rules.
3. Port the high-value n8n nodes first: Set, If, Switch, Merge, Split In
   Batches, Code, HTTP Request, Wait, Schedule, Webhook, Respond to Webhook,
   Edit Fields, Remove Duplicates, Sort, Limit, Aggregate.
4. Community nodes: the existing `.oaext` extension system already signs and
   versions packages — a node extension type is the n8n "community node"
   equivalent.

Acceptance: adding a node type is one manifest file plus one executor; the
palette, chat catalogue and validator all pick it up with no other edits.

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

Acceptance: from a single conversation a user can create, edit, run, debug,
publish, unpublish and delete a workflow, manage its credentials by reference,
and inspect runs — without opening another page. The only graph edits that
require the canvas are free-hand node positioning.

---

## 9. Order of work and effort

| Phase | Scope | Effort |
| --- | --- | --- |
| 1 | Expression engine + item read model | **done** |
| 2 | Items as the execution contract, conditions, long waits | **done** |
| 3 | Error workflow, pinned data in previews, partial execution | 1 week |
| 4 | Concurrency, pruning, queue mode, observability | 1–2 weeks |
| 5 | Manifest-driven node registry + core node ports | **registry done** (42 nodes, 5 triggers) |
| 6 | Tool registry, voice, chat actions for every resource | **surfaces done**; resource actions ongoing |

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
```

`reference/n8n` is a sparse checkout kept for reading only; it is excluded from
version control and is not built or imported by any package.
