import type { WorkflowDefinition } from './types';

export interface LayoutOptions {
  nodeWidth?: number;
  nodeHeight?: number;
  columnGap?: number;
  rowGap?: number;
  padding?: number;
}

export const LAYOUT_DEFAULTS = {
  nodeWidth: 244,
  nodeHeight: 84,
  columnGap: 140,
  rowGap: 48,
  padding: 80,
} as const;

/**
 * Hierarchical left-to-right layout: triggers in the first column, every node
 * one column after its deepest predecessor. Nodes in a cycle (no resolvable
 * predecessor order) are placed together in a trailing column. Within a column
 * rows are ordered by the average row of their predecessors, which keeps
 * straight flows straight without pulling in a layout dependency.
 *
 * Pure: the input definition is never mutated.
 */
export function layoutWorkflow(
  definition: WorkflowDefinition,
  options: LayoutOptions = {},
): WorkflowDefinition {
  const { nodeWidth, nodeHeight, columnGap, rowGap, padding } = {
    ...LAYOUT_DEFAULTS,
    ...options,
  };
  const triggerIds = definition.triggers.map((trigger) => trigger.id);
  const nodeIds = definition.nodes.map((node) => node.id);
  const known = new Set([...triggerIds, ...nodeIds]);

  const predecessors = new Map<string, string[]>();
  for (const id of known) predecessors.set(id, []);
  for (const edge of definition.edges) {
    if (edge.from === edge.to || !known.has(edge.from) || !known.has(edge.to)) continue;
    predecessors.get(edge.to)!.push(edge.from);
  }

  const depth = new Map<string, number>();
  for (const id of triggerIds) depth.set(id, 0);

  let pending = nodeIds.filter((id) => !depth.has(id));
  while (pending.length > 0) {
    const ready = pending.filter((id) =>
      predecessors.get(id)!.every((parent) => depth.has(parent)),
    );
    if (ready.length === 0) break;
    for (const id of ready) {
      const parents = predecessors.get(id)!;
      depth.set(
        id,
        parents.length === 0 ? 0 : Math.max(...parents.map((parent) => depth.get(parent)! + 1)),
      );
    }
    pending = pending.filter((id) => !depth.has(id));
  }
  // Unresolvable (cyclic) nodes share the last column.
  const resolvedMax = Math.max(0, ...depth.values());
  for (const id of pending) depth.set(id, resolvedMax + 1);

  const columns = new Map<number, string[]>();
  for (const id of [...triggerIds, ...nodeIds]) {
    const column = depth.get(id) ?? 0;
    columns.set(column, [...(columns.get(column) ?? []), id]);
  }

  const label = new Map<string, string>();
  for (const trigger of definition.triggers) label.set(trigger.id, trigger.name);
  for (const node of definition.nodes) label.set(node.id, node.name);
  for (const ids of columns.values()) {
    ids.sort((a, b) => label.get(a)!.localeCompare(label.get(b)!) || a.localeCompare(b));
  }

  const rowOf = new Map<string, number>();
  const indexColumn = () => {
    for (const ids of columns.values()) ids.forEach((id, index) => rowOf.set(id, index));
  };
  indexColumn();
  // Two barycenter sweeps: stable, and enough to straighten common shapes.
  for (let pass = 0; pass < 2; pass += 1) {
    for (const column of [...columns.keys()].sort((a, b) => a - b)) {
      const ids = columns.get(column)!;
      const score = new Map<string, number>();
      for (const id of ids) {
        const parents = predecessors.get(id)!;
        const knownRows = parents.filter((parent) => rowOf.has(parent));
        score.set(
          id,
          knownRows.length === 0
            ? rowOf.get(id)!
            : knownRows.reduce((sum, parent) => sum + rowOf.get(parent)!, 0) /
                knownRows.length,
        );
      }
      ids.sort(
        (a, b) => score.get(a)! - score.get(b)! || label.get(a)!.localeCompare(label.get(b)!),
      );
      indexColumn();
    }
  }

  const position = (id: string) => ({
    x: padding + (depth.get(id) ?? 0) * (nodeWidth + columnGap),
    y: padding + (rowOf.get(id) ?? 0) * (nodeHeight + rowGap),
  });

  return {
    ...definition,
    triggers: definition.triggers.map((trigger) => ({
      ...trigger,
      position: position(trigger.id),
    })),
    nodes: definition.nodes.map((node) => ({ ...node, position: position(node.id) })),
  };
}
