// Item-based graph preview engine. Mirrors n8n's data flow for the pure data
// nodes (one array of `{ json, pairedItem }` per output), so the editor can
// preview expressions and branch routing without a server. Side-effecting
// nodes (HTTP, agents, sub-workflows, waits) report "not simulated" instead of
// pretending to run; the backend runtime remains authoritative.

import {
  evaluateCondition,
  resolveValue,
  type EngineItem,
  type EvalContext,
} from './expressions';
import type { WorkflowDefinition, WorkflowNode } from '@/features/workflows/types';

export type { EngineItem };

export interface PreviewNodeRun {
  nodeId: string;
  name: string;
  status: 'succeeded' | 'failed' | 'skipped';
  items: EngineItem[];
  branches: Record<string, EngineItem[]>;
  error?: string;
}

export interface PreviewResult {
  runs: PreviewNodeRun[];
  /** Last output per node name, for `$node[...]` lookups. */
  outputs: Record<string, EngineItem[]>;
  errors: string[];
}

interface RunnerContext extends EvalContext {
  items: EngineItem[];
  executeOnce: boolean;
}

type Runner = (node: WorkflowNode, ctx: RunnerContext) => EngineItem[] | Record<string, EngineItem[]>;

const UNSUPPORTED = new Set([
  'agent',
  'prompt',
  'webhook',
  'subworkflow',
  'approval',
  'delay',
  'loop',
  'tool',
  'connector_action',
  'connector_trigger',
  'connector_search',
  'connector_resource',
  'code_agent',
  'code_search',
  'code_read',
  'code_patch',
  'code_test',
  'code_lint',
  'code_review',
  'git_commit',
  'create_pr',
  'verify',
  'evaluate',
  'quality_gate',
  'retry',
  'correct',
]);

function readPath(value: unknown, path: string): unknown {
  if (!path) return value;
  return path.split('.').reduce<unknown>((current, key) => {
    if (current === null || current === undefined) return undefined;
    if (Array.isArray(current)) return current[Number(key)];
    if (typeof current === 'object') return (current as Record<string, unknown>)[key];
    return undefined;
  }, value);
}

function asObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Not JSON — callers fall back to a single value.
    }
  }
  return {};
}

function withPair(items: EngineItem[], index: number): EngineItem[] {
  return items.map((item) => ({ ...item, pairedItem: item.pairedItem ?? index }));
}

const runners: Partial<Record<WorkflowNode['type'], Runner>> = {
  set: (node, ctx) =>
    ctx.items.map((item, index) => {
      const values = asObject(
        resolveValue(node.config.values ?? {}, { ...ctx, json: item.json, items: [item] }),
      );
      return {
        ...item,
        json: { ...item.json, ...values },
        pairedItem: item.pairedItem ?? index,
      };
    }),
  transform: (node, ctx) => {
    const raw = node.config.mapping ?? node.config.values ?? {};
    return ctx.items.map((item, index) => {
      const mapped = resolveValue(raw, { ...ctx, json: item.json, items: [item] });
      const json =
        typeof mapped === 'string'
          ? asObject(mapped)
          : mapped && typeof mapped === 'object'
            ? asObject(mapped)
            : item.json;
      return { ...item, json, pairedItem: index };
    });
  },
  filter: (node, ctx) => {
    const predicate = String(node.config.predicate ?? node.config.expression ?? 'true');
    return ctx.items.filter((item) =>
      evaluateCondition(predicate, { ...ctx, json: item.json, items: [item] }),
    );
  },
  condition: (node, ctx) => {
    const expression = String(node.config.expression ?? 'false');
    const truthy: EngineItem[] = [];
    const falsy: EngineItem[] = [];
    ctx.items.forEach((item, index) => {
      const pass = evaluateCondition(expression, { ...ctx, json: item.json, items: [item] });
      (pass ? truthy : falsy).push({ ...item, pairedItem: item.pairedItem ?? index });
    });
    return { true: truthy, false: falsy };
  },
  switch: (node, ctx) => {
    const routes = (() => {
      const raw = node.config.routes;
      if (Array.isArray(raw)) return raw as Array<{ name?: string; expression?: string }>;
      try {
        const parsed: unknown = JSON.parse(String(raw ?? '[]'));
        return Array.isArray(parsed) ? (parsed as Array<{ name?: string; expression?: string }>) : [];
      } catch {
        return [];
      }
    })();
    const branches: Record<string, EngineItem[]> = {};
    for (const route of routes) {
      if (!route.name) continue;
      branches[route.name] = ctx.items
        .filter((item) =>
          evaluateCondition(String(route.expression ?? 'false'), {
            ...ctx,
            json: item.json,
            items: [item],
          }),
        )
        .map((item) => ({ ...item }));
    }
    return branches;
  },
  limit: (_node, ctx) => {
    const max = Math.max(0, Number(_node.config.max_items ?? 10) || 0);
    const keep = String(_node.config.keep ?? 'first');
    const kept = keep === 'last' ? ctx.items.slice(-max) : ctx.items.slice(0, max);
    return withPair(kept, 0);
  },
  sort: (node, ctx) => {
    const field = String(node.config.field ?? '');
    const direction = String(node.config.direction ?? 'asc') === 'desc' ? -1 : 1;
    const numeric = Boolean(node.config.numeric);
    const sorted = [...ctx.items].sort((a, b) => {
      const left = readPath(a.json, field);
      const right = readPath(b.json, field);
      if (numeric) return (Number(left) - Number(right)) * direction;
      return String(left ?? '').localeCompare(String(right ?? '')) * direction;
    });
    return sorted.map((item, index) => ({ ...item, pairedItem: item.pairedItem ?? index }));
  },
  remove_duplicates: (node, ctx) => {
    const field = String(node.config.field ?? '');
    const seen = new Set<string>();
    const out: EngineItem[] = [];
    ctx.items.forEach((item, index) => {
      const key = JSON.stringify(field ? readPath(item.json, field) : item.json);
      if (seen.has(key)) return;
      seen.add(key);
      out.push({ ...item, pairedItem: item.pairedItem ?? index });
    });
    return out;
  },
  split_out: (node, ctx) => {
    const field = String(node.config.field ?? '');
    const out: EngineItem[] = [];
    ctx.items.forEach((item, index) => {
      const value = readPath(item.json, field);
      const list = Array.isArray(value) ? value : [value];
      for (const entry of list) {
        out.push({
          json:
            entry && typeof entry === 'object' && !Array.isArray(entry)
              ? (entry as Record<string, unknown>)
              : { value: entry },
          pairedItem: item.pairedItem ?? index,
        });
      }
    });
    return out;
  },
  merge: (_node, ctx) => withPair(ctx.items, 0),
  variable: (node, ctx) => {
    const name = String(node.config.name ?? 'value');
    const values = node.config.value;
    return ctx.items.map((item, index) => ({
      ...item,
      json: { ...item.json, [name]: resolveValue(values ?? null, { ...ctx, json: item.json }) },
      pairedItem: index,
    }));
  },
};

function branchFor(
  node: WorkflowNode | undefined,
  label: string | undefined,
  branches: Record<string, EngineItem[]>,
): EngineItem[] {
  if (!node) return branches.out ?? [];
  if (node.type === 'condition') {
    return label === 'false' || label === 'failure'
      ? (branches.false ?? [])
      : (branches.true ?? []);
  }
  if (node.type === 'switch' && label) return branches[label] ?? [];
  return branches.out ?? Object.values(branches)[0] ?? [];
}

function nodeRunner(type: WorkflowNode['type']): Runner | null {
  if (UNSUPPORTED.has(type)) return null;
  return runners[type] ?? null;
}

/**
 * Run a workflow preview from its triggers. Pure data nodes execute; anything
 * else is reported as failed with a "not simulated" message so the caller can
 * show the gap instead of a wrong result.
 */
export function executeWorkflowPreview(
  definition: WorkflowDefinition,
  options: {
    items?: EngineItem[];
    vars?: Record<string, unknown>;
    now?: Date;
    workflow?: { id?: string; name?: string };
  } = {},
): PreviewResult {
  const runs: PreviewNodeRun[] = [];
  const outputs: Record<string, EngineItem[]> = {};
  const errors: string[] = [];
  const byId = new Map<string, WorkflowNode>();
  for (const node of definition.nodes) byId.set(node.id, node);
  const triggerIds = new Set(definition.triggers.map((trigger) => trigger.id));

  const incoming = new Map<string, typeof definition.edges>();
  for (const node of definition.nodes) incoming.set(node.id, []);
  for (const edge of definition.edges) {
    if (!incoming.has(edge.to)) continue;
    incoming.get(edge.to)!.push(edge);
  }

  // Items arriving per node, grouped by the edge that carried them.
  const delivered = new Map<string, EngineItem[][]>();
  const deliveredEdges = new Map<string, Set<string>>();

  const push = (fromId: string, toId: string, edgeId: string, items: EngineItem[]) => {
    const list = delivered.get(toId) ?? [];
    list.push(items);
    delivered.set(toId, list);
    const edges = deliveredEdges.get(toId) ?? new Set<string>();
    edges.add(edgeId);
    deliveredEdges.set(toId, edges);
    void fromId;
  };

  const seed = options.items && options.items.length > 0 ? options.items : [{ json: {} }];
  for (const edge of definition.edges) {
    if (!triggerIds.has(edge.from)) continue;
    push(edge.from, edge.to, edge.id, seed);
  }

  const ready = definition.nodes.filter((node) => {
    const edges = incoming.get(node.id) ?? [];
    return edges.length === 0 || (deliveredEdges.get(node.id)?.size ?? 0) >= edges.length;
  });
  const queue = [...ready];
  const executed = new Set<string>();

  while (queue.length > 0) {
    const node = queue.shift()!;
    if (executed.has(node.id)) continue;
    executed.add(node.id);

    const inputs = delivered.get(node.id) ?? [];
    const items = inputs.flat();
    const executeOnce = Boolean(node.config.execute_once);
    const runner = nodeRunner(node.type);
    const ctx: RunnerContext = {
      json: items[0]?.json ?? {},
      items: executeOnce ? items.slice(0, 1) : items,
      vars: options.vars,
      now: options.now,
      workflow: options.workflow,
      nodeOutputs: Object.fromEntries(Object.entries(outputs).map(([name, list]) => [name, list])),
      executeOnce,
    };

    let run: PreviewNodeRun;
    if (!runner) {
      const message = `${node.name}: ${node.type} nodes are not simulated in the editor preview.`;
      run = { nodeId: node.id, name: node.name, status: 'failed', items: [], branches: {}, error: message };
      errors.push(message);
    } else {
      try {
        const produced = runner(node, ctx);
        const branches = Array.isArray(produced) ? { out: produced } : produced;
        const flat = Object.values(branches).flat();
        run = {
          nodeId: node.id,
          name: node.name,
          status: 'succeeded',
          items: flat,
          branches,
        };
        outputs[node.name] = flat;
      } catch (error) {
        const message = `${node.name}: ${error instanceof Error ? error.message : 'preview failed'}`;
        run = { nodeId: node.id, name: node.name, status: 'failed', items: [], branches: {}, error: message };
        errors.push(message);
      }
    }
    runs.push(run);

    for (const edge of definition.edges.filter((candidate) => candidate.from === node.id)) {
      const branchItems = branchFor(byId.get(node.id), edge.label, run.branches);
      push(node.id, edge.to, edge.id, branchItems);
    }

    for (const candidate of definition.nodes) {
      if (executed.has(candidate.id)) continue;
      const edges = incoming.get(candidate.id) ?? [];
      if (edges.length === 0) continue;
      if ((deliveredEdges.get(candidate.id)?.size ?? 0) >= edges.length && !queue.includes(candidate)) {
        queue.push(candidate);
      }
    }
  }

  return { runs, outputs, errors };
}
