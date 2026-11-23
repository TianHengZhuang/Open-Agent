// Small factories for graph pieces, shared by the editor, the n8n converter and
// the AI mutation engine. Kept separate from serialize.ts so importing one does
// not drag in the other.

import { nodeMeta, triggerMeta } from './node-catalog';
import type {
  TriggerType,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeType,
  WorkflowTrigger,
} from './types';

let counter = 0;

/** Deterministic-ish id factory (prefix + base36 time + counter). */
export function newId(prefix: string): string {
  counter += 1;
  return `${prefix}_${Date.now().toString(36)}${counter.toString(36)}`;
}

export function makeTrigger(type: TriggerType, at?: { x: number; y: number }): WorkflowTrigger {
  const meta = triggerMeta(type);
  void meta;
  return { id: newId('trg'), type, name: `New ${type} trigger`, config: {} };
}

export function makeNode(type: WorkflowNodeType, at?: { x: number; y: number }): WorkflowNode {
  const meta = nodeMeta(type);
  return {
    id: newId('n'),
    type,
    type_version: meta.version,
    name: `New ${meta.label}`,
    position: at ?? { x: 120, y: 120 },
    config: { ...meta.defaultConfig },
  };
}

export function edgeSourcePort(edge: Pick<WorkflowEdge, 'from_port' | 'label'>): string {
  return edge.from_port ?? edge.label ?? 'out';
}

export function edgeTargetPort(edge: Pick<WorkflowEdge, 'to_port'>): string {
  return edge.to_port ?? 'in';
}

export function makeEdge(
  from: string,
  to: string,
  port?: string,
  targetPort?: string,
): WorkflowEdge {
  const source = port ?? 'out';
  const target = targetPort ?? 'in';
  return {
    id: newId('e'),
    from,
    to,
    ...(source !== 'out' ? { from_port: source, label: source } : {}),
    ...(target !== 'in' ? { to_port: target } : {}),
    condition: { when: 'always' },
  };
}
