// Pure helpers behind the node detail view (NDV) and the canvas run overlay.
// Node output is a plain JSON object; data nodes return `{ items: [...] }`,
// which is the item list n8n calls "items". Everything here is side-effect
// free so the panes, badges and tests agree on one definition of "items".

import type { PinnedDatum, WorkflowDefinition } from './types';

/** The subset of a run record the data panes need. */
export interface NodeRunLike {
  node_id: string;
  node_name?: string;
  status?: string;
  inputs?: Record<string, unknown> | null;
  outputs?: Record<string, unknown>;
  error?: string | null;
  reason?: string | null;
  duration_ms?: number | null;
}

export interface DataItem {
  /** Item index, as shown in the pane (`0`, `1`, …). */
  index: number;
  json: unknown;
}

export type DataSource = 'run' | 'pinned' | 'none';

export interface InputSource {
  /** Edge label, or the upstream node name when the edge is unlabelled. */
  label: string;
  from_id?: string;
  source: DataSource;
  items: DataItem[];
  pinned_at?: string;
}

export interface NodeOutputData {
  source: DataSource;
  items: DataItem[];
  status?: string;
  error?: string | null;
  reason?: string | null;
  duration_ms?: number | null;
  pinned_at?: string;
  /** Pinned data is shown, but a newer run result exists underneath. */
  newer_run: boolean;
}

/**
 * Normalise an engine output value into items.
 * `undefined`/`null`/`{}` → no items; arrays are item lists; an object with an
 * array `items` field (data nodes) exposes that list; anything else is one item.
 */
export function toItems(value: unknown): DataItem[] {
  if (value === undefined || value === null) return [];
  if (Array.isArray(value)) return value.map((json, index) => ({ index, json }));
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if (Object.keys(record).length === 0) return [];
    if (Array.isArray(record.items)) return toItems(record.items);
    return [{ index: 0, json: value }];
  }
  return [{ index: 0, json: value }];
}

/** Number of items a node produced, for the canvas badge. */
export function itemCount(value: unknown): number {
  return toItems(value).length;
}

export function resolveNodeInputs({
  definition,
  nodeId,
  run,
  runs = [],
  pinned,
}: {
  definition: WorkflowDefinition;
  nodeId: string;
  /** Run record for the node being inspected. */
  run?: NodeRunLike | null;
  /** All run records from the last workflow run (upstream fallback). */
  runs?: NodeRunLike[];
  pinned?: Record<string, PinnedDatum>;
}): InputSource[] {
  const incoming = definition.edges.filter((edge) => edge.to === nodeId);
  const labelFor = (key: string): { label: string; from_id?: string } => {
    const edge = incoming.find((e) => (e.label ?? 'input') === key);
    if (!edge) return { label: key };
    const upstream = runs.find((r) => r.node_id === edge.from);
    const node = definition.nodes.find((n) => n.id === edge.from);
    const trigger = definition.triggers.find((t) => t.id === edge.from);
    return {
      label: edge.label || upstream?.node_name || node?.name || trigger?.name || key,
      from_id: edge.from,
    };
  };

  // The engine records exactly what each node received, keyed by edge label
  // (or "input" for the first upstream). Prefer that over reconstructing.
  const recorded = Object.entries(run?.inputs ?? {});
  if (recorded.length > 0) {
    return recorded.map(([key, value]) => {
      const { label, from_id } = labelFor(key);
      return { label, from_id, source: 'run' as const, items: toItems(value) };
    });
  }

  if (incoming.length === 0) return [];

  return incoming.map((edge) => {
    const upstreamRun = runs.find((r) => r.node_id === edge.from);
    if (upstreamRun) {
      return {
        label: edge.label || upstreamRun.node_name || edge.from,
        from_id: edge.from,
        source: 'run' as const,
        items: toItems(upstreamRun.outputs),
      };
    }
    const pin = pinned?.[edge.from];
    if (pin) {
      return {
        label: edge.label || edge.from,
        from_id: edge.from,
        source: 'pinned' as const,
        items: toItems(pin.outputs),
        pinned_at: pin.pinned_at,
      };
    }
    const node = definition.nodes.find((n) => n.id === edge.from);
    const trigger = definition.triggers.find((t) => t.id === edge.from);
    return {
      label: edge.label || node?.name || trigger?.name || edge.from,
      from_id: edge.from,
      source: 'none' as const,
      items: [],
    };
  });
}

export function resolveNodeOutput({
  run,
  pinned,
}: {
  run?: NodeRunLike | null;
  pinned?: PinnedDatum;
}): NodeOutputData {
  const runData: NodeOutputData = {
    source: run ? 'run' : 'none',
    items: toItems(run?.outputs),
    status: run?.status,
    error: run?.error ?? null,
    reason: run?.reason ?? null,
    duration_ms: run?.duration_ms ?? null,
    newer_run: false,
  };
  // Pinned data wins over run data, like n8n: the pin is what the author chose
  // to expose. Unpin to reveal the latest run without losing it.
  if (pinned) {
    return {
      ...runData,
      source: 'pinned',
      items: toItems(pinned.outputs),
      status: pinned.status ?? runData.status,
      error: null,
      pinned_at: pinned.pinned_at,
      newer_run: run !== undefined && run !== null,
    };
  }
  return runData;
}

/** Copy an item as pretty JSON (used by the pane's copy buttons). */
export function itemJson(item: DataItem): string {
  return JSON.stringify(item.json, null, 2);
}
