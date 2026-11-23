# OpenAgent → n8n প্যারিটি রোডম্যাপ

**লক্ষ্য:** OpenAgent-কে এমন একটি চ্যাট-ফার্স্ট অটোমেশন প্ল্যাটফর্মে পরিণত করা, যেখানে n8n-এর ইঞ্জিন-শক্তি (item data model, expression evaluator, production runtime) থাকবে, কিন্তু ব্যবহারকারীর প্রধান ইন্টারফেস হবে একটি শক্তিশালী চ্যাটবক্স — লেখা বা ভয়েসে ওয়ার্কফ্লো তৈরি, এডিট, ডিবাগ ও চালানো যাবে।

**নর্থ স্টার (৬ মাস):**

- প্রম্পট থেকে রানযোগ্য ওয়ার্কফ্লো: p50 ≤ ৯০ সেকেন্ড।
- চ্যাটে করা প্রতিটি গ্রাফ পরিবর্তন এক ধাপে undo (১০০%)।
- n8n ইমপোর্ট করা ওয়ার্কফ্লোর ≥৮০% ম্যানুয়াল সম্পাদনা ছাড়াই ভ্যালিডেট + রান।
- নন-LLM নোডে রান p95 < ৩০০ ms; প্রতি এক্সিকিউশনে item-level ট্রেস সংরক্ষিত।

---

## ১. n8n সোর্স স্টাডি → OpenAgent টার্গেট ম্যাপ

| n8n-এর ধারণা | n8n সোর্স | OpenAgent-এ গন্তব্য |
|---|---|---|
| Item data model (`INodeExecutionData`, `pairedItem`) | `packages/workflow/src/interfaces.ts`, `run-execution-data-factory.ts` | নতুন `apps/api/src/openagent/runtime/items.py`; `apps/web/src/features/workflows/node-data.ts` |
| Execution loop + node stack | `packages/core/src/execution-engine/workflow-execute.ts` | `apps/api/src/openagent/runtime/local_engine.py` (`NodeOutcome`, `RunContext`) |
| Lifecycle hooks (persistence, progress, error workflow) | `docs/backend/workflow-execution.md`, `execution-lifecycle-hooks.ts` | `local_engine.py`-এর বাইরে নতুন `runtime/lifecycle.py` + `api/v1/workflows.py` স্ট্রিমিং |
| Queue mode (main → Bull/Redis → worker) | `packages/cli/src/scaling/`, `docs/backend/how-n8n-runs.md` | নতুন `apps/api/src/openagent/worker.py` + RQ/Redis (ফ্ল্যাগ-গেটেড) |
| Expression sandbox (`{{ }}`, `$json`, `$input`, `$node`, `$vars`, `$now`) | `packages/workflow/src/expression.ts` | নতুন `packages/expression` (TS) + Python resolver (আজ `local_engine.resolve`) |
| Credential vault | `packages/core/src/credentials.ts` | `apps/api/src/openagent/connectors/crypto.py`, `api/v1/integrations.py` |
| Draft vs published version | workflow versioning in `packages/cli/src/workflows/` | `apps/api/src/openagent/services/workflow_definition.py`, `api/v1/workflows.py` |
| n8n JSON interchange | `packages/cli/src/import/` | `apps/web/src/features/workflows/n8n-format.ts` |
| Assistant (chat → graph) | `packages/frontend/editor-ui` assistant | `apps/web/src/features/workflows/mutations.ts`, `components/workflows/KoroPanel.tsx`, `services/workflow_chat.py` |

রেফারেন্স: [workflow-execution.md](https://github.com/n8n-io/n8n/blob/master/docs/backend/workflow-execution.md) · [how-n8n-runs.md](https://github.com/n8n-io/n8n/blob/master/docs/backend/how-n8n-runs.md) · [interfaces.ts](https://github.com/n8n-io/n8n/blob/master/packages/workflow/src/interfaces.ts) · [n8n.io](https://n8n.io/)

---

## ২. বর্তমান অবস্থা (অডিট, ২০২৬-১১-২২)

**আছে:**

- চ্যাট হাব ও মাল্টি-থ্রেড হিস্ট্রি (`components/assistant/`), `+` মেনু, `@` mention (node / `$json` / credential / `$vars`), ভয়েস, মডেল পিল।
- local + BYOK মডেল ইঞ্জিন, discovery/health/favorites (`lib/ai-models.ts`, `features/koro/use-chat-model.ts`)।
- SSE স্ট্রিমিং (`features/koro/chat-stream.ts`, `api/v1/koro.py`)।
- typed গ্রাফ মিউটেশন, অটো-লেআউট, diff, atomic undo/redo, cURL→HTTP (`features/workflows/mutations.ts`, `curl.ts`)।
- NDV + data pinning (`NodeDetailView.tsx`, `node-data.ts`)।
- n8n JSON কমন-নোড ইমপোর্ট/এক্সপোর্ট, warning-সহ (`n8n-format.ts`)।
- ইঞ্জিন: টপোলজিক্যাল রান, retry policy, `on_error: continue`, sub-workflow, approval/wait, cron (`runtime/local_engine.py`, `schedule_runner.py`)।
- ১৬২ নোড ডেফিনিশন (`node-catalog.ts`), এনক্রিপ্টেড ক্রেডেনশিয়াল (`connectors/crypto.py`)।

**নেই:**

- wrapper-ভিত্তিক item মডেল, per-item looping/`executeOnce`, `pairedItem` lineage।
- পূর্ণ এক্সপ্রেশন ইভ্যালুয়েটর ও NDV-তে লাইভ প্রিভিউ (ওয়েব দিকে)।
- lifecycle hook abstraction, queue/worker, durable wait→resume, error workflow।
- vector store / embeddings / document loader, Python code sandbox, AI agent sub-node ports, IF multi-rule UI।
- ১০০% n8n JSON ম্যাপিং, এবং একক চ্যাট-কমান্ড শেল (আজ টুলবার ছড়ানো)।

---

## ৩. গ্যাপ ম্যাট্রিক্স

| সক্ষমতা | n8n | OpenAgent | ফেজ |
|---|---|---|---|
| item wrapper + `pairedItem` | ✅ | ⚠️ প্লেইন list | ১ |
| per-item loop / `executeOnce` | ✅ | ❌ | ১ |
| `$input/$node/$now/$execution` | ✅ | ⚠️ আংশিক | ২ |
| NDV লাইভ এক্সপ্রেশন প্রিভিউ | ✅ | ❌ | ২ |
| lifecycle hooks + live node progress | ✅ | ⚠️ run log | ৩ |
| queue/worker scaling | ✅ | ❌ | ৩ |
| durable wait/resume | ✅ | ⚠️ সীমিত | ৩ |
| error workflow | ✅ | ❌ | ৩ |
| AI sub-node ports | ✅ | ❌ | ৪ |
| vector store / embeddings / loaders | ✅ | ❌ | ৪ |
| Python code sandbox | ✅ | ❌ | ৪ |
| retry / onError / alwaysOutputData UI | ✅ | ⚠️ আংশিক | ১+৪ |
| credentials vault + `$vars` | ✅ | ✅ | — |
| draft/published versioning | ✅ | ✅ | — |
| n8n JSON ১০০% | ✅ | ⚠️ সাবসেট | ৬ |
| চ্যাট-ফার্স্ট শেল | ✅ | ⚠️ | ৫ |

---

## ৪. ফেজ রোডম্যাপ

### ফেজ ০ — বেসলাইন ও ফ্রিজ (৩–৫ দিন)

- `docs/engine-data-contract.md`: প্রতি নোড টাইপের ইনপুট/আউটপুট শেপ।
- ১০টি গোল্ডেন ওয়ার্কফ্লো ফিক্সচার + snapshot টেস্ট (আজকের আচরণ)।
- টেলিমেট্রি: node timing, item count, failure class।
- **Acceptance:** ফিক্সচার সব পাস; কোনো আচরণ পরিবর্তন নয়।

### ফেজ ১ — Item Data Model (২–৩ সপ্তাহ)

1. `runtime/items.py`: `Item(json, binary, paired_item, error, metadata)`, `to_wire/from_wire`, legacy normalization।
2. `local_engine.py`: নোড I/O item অ্যারে; per-item loop ডিফল্ট, `config.execute_once` honor।
3. `paired_item` ট্র্যাকিং, item index ম্যাপ।
4. ওয়েব: `node-data.ts` wrapper-aware; NDV output-এ item index/lineage; workflow settings-এ `execute_once` টগল।
5. `n8n-format.ts`: wrapper ↔ plain json রূপান্তর।
- **Acceptance:** গোল্ডেন ফিক্সচার item-shape সহ পাস; `executeOnce` integration test; পুরনো saved definition অপরিবর্তিত চলে; ফ্ল্যাগ `items_v2` দিয়ে রোলব্যাক।

### ফেজ ২ — Expression Engine (২–৩ সপ্তাহ)

1. `packages/expression`: tokenizer → AST → sandboxed evaluator; `$json`, `$input.first/last/all`, `$node["Name"].json`, `$vars`, `$now`, `$execution.id`, `$workflow.name`।
2. Python resolver সমমানের (একই টেস্ট-ভেক্টর, AST ক্যাশ)।
3. NDV: প্রতি প্যারামিটারে **Fixed / Expression** টগল + লাইভ প্রিভিউ (debounce), undefined-path warning, ফল-টাইপ ব্যাজ।
4. এররে নোড নাম + expression + item index; "Fix with Koro"-তে structured error।
5. টেবিল-ড্রিভেন টেস্ট (truthiness, null-safe, array map, date/math)।
- **Acceptance:** web/python অভিন্ন ফল; প্রিভিউ ≤১৫০ ms; `fetch`/`process` ব্লকড, timeout + memory cap।

### ফেজ ৩ — Execution Runtime (৩–৪ সপ্তাহ)

1. `runtime/lifecycle.py`: `workflow_before/after`, `node_before/after`; persistence ও progress streaming hook-এ; ইঞ্জিন DB-মুক্ত।
2. Retry/onError/alwaysOutputData per-item সেমান্টিকস + NDV Settings UI।
3. Durable wait: `waiting_executions` + resume endpoint + TTL janitor (approval ও `Wait` একই পথ)।
4. Error workflow: `error_workflow_id`; payload `{execution, node, error, items}`।
5. ঐচ্ছিক Redis/RQ queue + `worker` entrypoint; ফ্ল্যাগ বন্ধে আজকের in-process path।
6. Execution detail: item counts, timing waterfall, "replay from this node"।
- **Acceptance:** দুই মোডে একই ফল; wait→resume crash-safe test; error workflow E2E; ইঞ্জিনে DB import নেই।

### ফেজ ৪ — নোড প্যারিটি (৪–৬ সপ্তাহ, ব্যাচে)

- **A — Logic:** IF multi-rule AND/OR, Switch fallback, Merge (append / by-key / by-position / choose-branch), Split Out, Aggregate, Limit, Sort, Remove Duplicates।
- **B — Data & Code:** Code (JS isolate; Python আলাদা রানার), Date & Time, Crypto, HTML/XML/Markdown extract, HTTP pagination + binary + auth presets।
- **C — AI/Agentic:** AI Agent sub-node ports (Chat Model / Memory / Tools / Output Parser), LLM Chain, Embeddings + Vector Store (pgvector), Document Loader/Splitter, Human-in-the-Loop।
- প্রতি ব্যাচ: ক্যাটালগ → ইঞ্জিন → NDV → টেস্ট → n8n ম্যাপিং।
- **Acceptance:** প্রতি নোডে ≥১ item-level unit test + ১ E2E টেমপ্লেট; ম্যাপিং কভারেজ রিপোর্ট।

### ফেজ ৫ — চ্যাট-ফার্স্ট শেল ও অ্যাকশন রেজিস্ট্রি (৩–৪ সপ্তাহ)

- নেভিগেশন: **Chat / Canvas / Executions / Settings**; বাকি সব চ্যাট কমান্ড।
- `/` system action (`/run`, `/publish`, `/export`, `/import`), `@` context, Build/Plan/Ask মোড।
- স্টুডিওতে চ্যাট ড্রয়ার ডিফল্ট-ক্লোজড, Cmd/Ctrl+J।
- **Acceptance:** প্রতিটি core action চ্যাট থেকে সম্পাদনযোগ্য; প্রতি mutation এক undo; keyboard-only ফ্লো পাস।

### ফেজ ৬ — n8n কম্প্যাটিবিলিটি ও প্রোডাকশন হার্ডেনিং (২ সপ্তাহ)

- Importer: ফুল টাইপ ম্যাপিং, `typeVersion` মাইগ্রেশন, sub-node গ্রাফ; export-এ credential reference/pinData/settings।
- "Import report" ডায়ালগ (কী ম্যাপ হলো, কী বাদ, কেন)।
- Credentials rotation, last-used, per-workflow scope; secret redaction টেস্ট।
- Retention policy, execution redaction, rate limits, webhook test vs production URL।
- **Acceptance:** ২০টি বাস্তব n8n টেমপ্লেট ইমপোর্ট→validate→run; গোপন মান কোনো লগে নেই (অটোমেটেড grep)।

---

## ৫. চ্যাট-ফার্স্ট আর্কিটেকচার

**এক রেজিস্ট্রি, দুই এন্ট্রি পয়েন্ট (চ্যাট টুল + UI কমান্ড)।**

```ts
type Action<I, O> = {
  name: 'workflow.add_node' | 'workflow.connect' | 'execution.run' | 'workflow.publish';
  schema: ZodSchema<I>;              // LLM tool schema-ও এটাই
  permission: Permission;            // workflow:update, execution:run, ...
  mode: 'build' | 'plan' | 'both';   // Plan মোডে শুধু প্রস্তাব
  preview(i: I): DiffPreview;        // চ্যাটে দেখানো হবে
  apply(i: I): Promise<O>;           // editor.replace — এক undo স্ন্যাপশট
};
```

- **নিরাপত্তা:** `apply` permission-gated; hosted-provider override-এর server-side guard অপরিবর্তিত।
- **Undo:** প্রতি চ্যাট-বিল্ড = এক `editor.replace(next, { label })`।
- **টুল কলিং:** মডেলকে schema দেওয়া হবে; লোকাল মডেলে tool support না থাকলে JSON-plan + ভ্যালিডেটর fallback।
- **Debug লুপ:** `ExecutionLog`/NDV → structured failure → root cause + patch → apply → re-run।
- **ভয়েস:** transcription → একই কম্পোজার; apply-এর আগে নিশ্চিতকরণ বাধ্যতামূলক।

---

## ৬. মাইগ্রেশন

1. `schema_version` 1.0 → 2.0: persisted config অপরিবর্তিত; শুধু runtime wire-format item wrapper — saved ওয়ার্কফ্লো মাইগ্রেশন নেই।
2. Execution output: নতুন `items` ফিল্ড পাশাপাশি (dual-read, single-write); পুরনো execution adapter দিয়ে পড়া।
3. Expression: পুরনো `{{x}}` অপরিবর্তিত; `$json` additive।
4. n8n unmapped নোড `transform` + `n8n_parameters` (বর্তমান আচরণ), ফেজ ৪-এ ধীরে ম্যাপ।

---

## ৭. টেস্ট ও গেট

| স্তর | কী | কমান্ড |
|---|---|---|
| Unit (web) | item normalization, expression, mutations, n8n mapper | `pnpm --filter @openagent/web test` |
| Unit (api) | items, resolver, retry/onError, wait resume | `pytest apps/api/tests` |
| Contract | একই এক্সপ্রেশন দুই রানটাইমে অভিন্ন | shared JSON vectors |
| E2E | chat → build → run → fail → fix → undo | Playwright (ফেজ ৫) |
| গেট | typecheck 0, test সব পাস, build সফল | প্রতি PR |

---

## ৮. ঝুঁকি, রোলব্যাক, অ-লক্ষ্য

| ঝুঁকি | প্রশমন |
|---|---|
| item মডেলে বিদ্যমান ওয়ার্কফ্লো ভাঙা | ফেজ ০ ফিক্সচার + adapter + `items_v2` ফ্ল্যাগ |
| sandbox escape | আলাদা প্রসেস/isolate, allowlist, timeout, memory cap |
| queue জটিলতা | ফ্ল্যাগ-গেটেড, in-process ডিফল্ট |
| ভুল চ্যাট মিউটেশন | preview + permission + এক-স্ন্যাপশট undo |
| tool schema drift | রেজিস্ট্রি থেকেই tool definition generate |

**অ-লক্ষ্য:** n8n-এর ৪০০+ ইন্টিগ্রেশন, n8n UI ক্লোন, billing/mobile।

---

## ৯. প্রথম ১০ কার্যদিবস

1. `docs/engine-data-contract.md` (১৬২ নোডের শেপ)।
2. ১০টি গোল্ডেন ফিক্সচার + snapshot টেস্ট।
3. `runtime/items.py` + normalization টেস্ট।
4. `local_engine.py`-এ `execute_once` + per-item dispatch (ফ্ল্যাগ)।
5. `node-data.ts` wrapper-aware + NDV item index।
6. `packages/expression` skeleton (`$json`, `$input.first()`)।
7. NDV Fixed/Expression টগল + প্রিভিউ।
8. ফেজ ১ রিগ্রেশন রিপোর্ট, ফেজ ২ কিক-অফ।

---

## ১০. KPI

- চ্যাট থেকে সম্পাদিত অ্যাকশনের অনুপাত (≥৭০%)।
- প্রম্পট→রানযোগ্য ওয়ার্কফ্লো সাফল্য (≥৮৫%)।
- চ্যাট-বিল্ডে undo-যোগ্যতা (১০০%), গড় ধাপ ≤২।
- n8n ইমপোর্ট সাকসেস (≥৮০%, warning-সহ)।
- failure→fix সময় (<৩ মিনিট), রান p95।
