# Connection & Data-Flow Contract (schema 1.1)

Status: **contract implemented and validated; runtime unchanged.** The current
execution engine is sequential and label-based (Prompt 08 owns item execution).
No binary handling, item loop, error-port runtime, or tracing emitter exists yet.

## 1. Connection model

Edges remain a flat list on the definition. Legacy edges are valid forever:

```json
{ "id": "e1", "from": "trg", "to": "n_1", "label": "true" }
```

Schema 1.1 adds optional typed endpoints:

```json
{
  "id": "e1",
  "from": "n_cond",
  "from_port": "true",
  "to": "n_send",
  "to_port": "in",
  "connection_type": "main",
  "label": "true",
  "condition": { "when": "always" },
  "metadata": { "ui": "branch" }
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable connection id (unique within a definition). |
| `from`, `to` | yes | Source/target trigger or node id. |
| `from_port` | no | Source output port name. Falls back to `label` (condition/switch) then the first output. |
| `to_port` | no | Target input port name. Falls back to the required/first input. |
| `connection_type` | no | `main` (default) or `error`. `error` is rejected unless the source declares an `error` output; no builtin does yet. |
| `label` | no | Legacy branch/port label, retained for the current engine and n8n export. |
| `condition` | no | Existing `{when, expression}` edge condition. |
| `metadata` | no | Non-executed object for UI/analytics. |

Resolution rules (identical in Python and TypeScript):

1. If `from_port` is present it must exist on the source node.
2. Otherwise, a `label` that names a source port is used (condition `true`/`false`,
   switch route names).
3. Otherwise the source's first declared output is used (`out` for most nodes).
4. Target: `to_port` if present, else the first required input, else the first input.
5. Triggers expose one `out` port and cannot be edge targets.
6. Switch output ports are dynamic: `config.routes[].name` plus `default`.

## 2. Port definitions

Every registry entry carries `input_ports` and `output_ports`; legacy `outputs`
and `has_input` stay for the canvas. A port is:

```json
{
  "name": "in",
  "label": "Input",
  "direction": "input",
  "data_types": ["items"],
  "required": true,
  "multiple": false,
  "description": null,
  "default_value": null,
  "dynamic": false
}
```

- `data_types`: `any`, `items`, `json`, `text`, `number`, `boolean`, `binary`.
- `required` inputs must be connected once the node is reachable through any input.
- `multiple` controls fan-in/fan-out. Inputs default to the node's `fanIn` flag
  (only `merge` today); outputs default to multiple.
- `dynamic` marks switch outputs resolved from config.
- Departments/AI planners read the same generated JSON, so parameter and port
  metadata never diverge.

### Generation and drift guard

Single source: `apps/web/src/features/workflows/node-catalog.ts`.
Generated copies:

- `apps/web/src/features/workflows/node-registry.json`
- `apps/api/src/openagent/registry/node-registry.json`

Regenerate and verify:

```bash
cd apps/web
WRITE_NODE_REGISTRY=1 npx vitest run src/features/workflows/registry.test.ts
npx vitest run src/features/workflows/registry.test.ts   # consistency check
```

The consistency test fails if either JSON copy differs from the catalog, if a
duplicate `type@version` exists, if defaults reference unknown fields, or if a
port has an unknown direction/type.

## 3. Validation rules

Blocking errors (with `node_id`, `edge_id`, `field` where applicable):

| Code | Condition |
|---|---|
| `UNKNOWN_EDGE_SOURCE` / `UNKNOWN_EDGE_TARGET` | Endpoint id missing. |
| `UNKNOWN_SOURCE_PORT` / `UNKNOWN_TARGET_PORT` | Named port absent. |
| `INVALID_EDGE_PORT` | Empty/non-string `from_port`/`to_port`. |
| `INVALID_EDGE_METADATA` | `metadata` is not an object. |
| `UNSUPPORTED_CONNECTION_TYPE` | Not `main`/`error`. |
| `CONNECTION_TYPE_UNAVAILABLE` | `error` edge but source has no `error` port. |
| `INCOMPATIBLE_PORT_TYPES` | No source type accepted by the target port. |
| `DUPLICATE_EDGE` | Same source port → target port connection repeated. |
| `SELF_LOOP` | Source equals target. |
| `EDGE_INTO_TRIGGER` | Trigger has an incoming edge. |
| `MULTIPLE_INBOUND_EDGES` | A non-multiple input receives >1 connection. |
| `PORT_FAN_OUT_EXCEEDED` | A non-multiple output has >1 connection. |
| `NODE_REQUIRED_INPUT_MISSING` | Reachable node uses one input but leaves a required input empty. |

Warnings: `INVALID_EDGE_PORT` for an unknown legacy condition/switch label,
`NODE_WITHOUT_OUTGOING`, `TRIGGER_WITHOUT_EDGES`, existing graph warnings.

Type compatibility matrix:

| Source \ target | any | items | json | text | number | boolean | binary |
|---|---|---|---|---|---|---|---|
| any | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| items | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| json | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| text | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| number | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| boolean | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ |
| binary | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |

All builtin flow ports currently declare `items` (the engine passes item-shaped
values), so existing workflows do not become invalid. Compatibility is enforced
for future typed ports and is covered by both test suites.

## 4. Item / payload contract (types only)

```ts
type DataItem = {
  json: Record<string, unknown>;
  binary?: Record<string, { mime_type; file_name?; data_ref; size_bytes? }>;
  paired_item?: number | number[];
  error?: { code?; message; retriable? };
};

type PortPayload = {
  port: string;
  connection_type: 'main' | 'error';
  items: DataItem[];
};
```

Binary values are references into a future store, never inline base64. The
event contract adds `execution_id`, `node_id`, `port`, `item_index`, `attempt`,
`status`, counts, duration and a redacted error. Python models live in
`services/workflow_schema.py`; TypeScript types live in
`features/workflows/types.ts`. Neither runtime consumes them yet.

## 5. Schema and migration

- No database change: edges live in the `workflow_versions.definition` JSON.
- No destructive rewrite: 1.0 and 1.1 definitions remain readable; port fields
  are optional and are only emitted for non-default branches.
- Legacy `label` is preserved for the engine and n8n export, so existing
  workflows execute exactly as before.
- `from_port` is used by the engine when present, with `label` fallback; no new
  execution semantics are introduced.

## 6. Import/export

- n8n main-output indices map to registry output ports (condition `0/1` →
  `true/false`, switch index → route name).
- OpenAgent export keeps `from_port`, `to_port`, `connection_type`, `metadata`
  and `condition`.
- Unknown n8n connections still import as legacy edges with a warning.
