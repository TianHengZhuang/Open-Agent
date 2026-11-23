// Typed graph mutations for chat-driven edits. Every mutation is validated,
// applied to a copy, and laid out; the caller pushes the result onto the
// editor as one `replace` (one undo step). Pure and testable.

import { layoutWorkflow } from './auto-layout';
import { curlToMutation } from './curl';
import { extractExpressions } from './expressions';
import { makeEdge, makeNode, makeTrigger, newId } from './serialize';
import { edgeSourcePort, edgeTargetPort } from './factory';
import {
  emptyDefinition,
  type TriggerType,
  type WorkflowDefinition,
  type WorkflowEdge,
  type WorkflowNode,
  type WorkflowNodeType,
} from './types';

export type GraphMutation =
  | {
      op: 'add_node';
      id?: string;
      type: WorkflowNodeType;
      label?: string;
      position?: { x: number; y: number };
      config?: Record<string, unknown>;
      disabled?: boolean;
    }
  | {
      op: 'update_node';
      nodeId: string;
      label?: string;
      config?: Record<string, unknown>;
      disabled?: boolean;
      credential_id?: string | null;
    }
  | { op: 'remove_node'; nodeId: string }
  | {
      op: 'connect_nodes';
      sourceNodeId: string;
      sourcePort?: string;
      targetNodeId: string;
      targetPort?: string;
      label?: string;
    }
  | { op: 'disconnect_nodes'; edgeId?: string; sourceNodeId?: string; targetNodeId?: string }
  | {
      op: 'set_workflow_meta';
      name?: string;
      tags?: string[];
      settings?: Record<string, unknown>;
    }
  | { op: 'auto_layout_graph' }
  | { op: 'import_curl'; curl: string; nodeId?: string; label?: string }
  | {
      op: 'apply_node_fix';
      nodeId: string;
      label?: string;
      config?: Record<string, unknown>;
      disabled?: boolean;
    };

export interface NodeChange {
  id: string;
  name: string;
  type: WorkflowNodeType;
}

export interface NodeUpdate extends NodeChange {
  changed: string[];
}

export interface GraphDiff {
  nodesAdded: NodeChange[];
  nodesRemoved: NodeChange[];
  nodesUpdated: NodeUpdate[];
  edgesAdded: { from: string; to: string }[];
  edgesRemoved: { from: string; to: string }[];
  expressionsAdded: number;
  /** Node names whose config still references an unbound credential. */
  credentialsRequired: string[];
}

export interface MutationOutcome {
  definition: WorkflowDefinition;
  diff: GraphDiff;
  /** Errors for skipped operations, in input order. */
  errors: string[];
  /** Metadata that lives on the workflow record rather than the definition. */
  meta: { name?: string; tags?: string[] };
}

const CREDENTIAL_KEY = /(^|_)credential(_id)?$/i;

function clone(definition: WorkflowDefinition): WorkflowDefinition {
  return JSON.parse(JSON.stringify(definition)) as WorkflowDefinition;
}

function nodeChange(node: WorkflowNode): NodeChange {
  return { id: node.id, name: node.name, type: node.type };
}

export function edgePairs(edges: WorkflowEdge[]): string[] {
  return edges.map((edge) => `${edge.from}->${edge.to}`);
}

function nodeConfigChanged(before: WorkflowNode, after: WorkflowNode): string[] {
  const changed: string[] = [];
  if (before.name !== after.name) changed.push('label');
  if (JSON.stringify(before.config) !== JSON.stringify(after.config)) changed.push('config');
  if (!!before.disabled !== !!after.disabled) changed.push('disabled');
  return changed;
}

/** Nodes whose config leaves a credential slot empty. */
export function missingCredentials(definition: WorkflowDefinition): string[] {
  const names: string[] = [];
  for (const node of definition.nodes) {
    for (const [key, value] of Object.entries(node.config ?? {})) {
      if (!CREDENTIAL_KEY.test(key)) continue;
      if (value === undefined || value === null || value === '') {
        names.push(node.name || node.type);
        break;
      }
    }
  }
  return names;
}

function countExpressions(nodes: WorkflowNode[], base?: WorkflowNode[]): number {
  const before = base ? countExpressions(base) : 0;
  const after = nodes.reduce((total, node) => total + extractExpressions(node.config).length, 0);
  return Math.max(0, after - before);
}

/** Compare two graphs for the chat's diff card. */
export function summarizeDiff(
  before: WorkflowDefinition,
  after: WorkflowDefinition,
): GraphDiff {
  const beforeNodes = new Map(before.nodes.map((node) => [node.id, node]));
  const afterNodes = new Map(after.nodes.map((node) => [node.id, node]));

  const nodesAdded = after.nodes.filter((node) => !beforeNodes.has(node.id)).map(nodeChange);
  const nodesRemoved = before.nodes.filter((node) => !afterNodes.has(node.id)).map(nodeChange);
  const nodesUpdated: NodeUpdate[] = [];
  const grown: WorkflowNode[] = [];
  for (const node of after.nodes) {
    const previous = beforeNodes.get(node.id);
    if (!previous) {
      grown.push(node);
      continue;
    }
    const changed = nodeConfigChanged(previous, node);
    if (changed.length > 0) nodesUpdated.push({ ...nodeChange(node), changed });
    if (changed.includes('config')) grown.push(node);
  }

  const beforeEdges = new Set(edgePairs(before.edges));
  const afterEdges = new Set(edgePairs(after.edges));
  const edgesAdded = after.edges
    .filter((edge) => !beforeEdges.has(`${edge.from}->${edge.to}`))
    .map((edge) => ({ from: edge.from, to: edge.to }));
  const edgesRemoved = before.edges
    .filter((edge) => !afterEdges.has(`${edge.from}->${edge.to}`))
    .map((edge) => ({ from: edge.from, to: edge.to }));

  return {
    nodesAdded,
    nodesRemoved,
    nodesUpdated,
    edgesAdded,
    edgesRemoved,
    expressionsAdded: countExpressions(grown),
    credentialsRequired: missingCredentials(after),
  };
}

/** One-line summary for the action card ("4 nodes added · 3 connections"). */
export function describeDiff(diff: GraphDiff): string {
  const parts: string[] = [];
  if (diff.nodesAdded.length > 0) {
    parts.push(`${diff.nodesAdded.length} node${diff.nodesAdded.length === 1 ? '' : 's'} added`);
  }
  if (diff.nodesRemoved.length > 0) parts.push(`${diff.nodesRemoved.length} removed`);
  if (diff.nodesUpdated.length > 0) parts.push(`${diff.nodesUpdated.length} updated`);
  if (diff.edgesAdded.length > 0) {
    parts.push(`${diff.edgesAdded.length} connection${diff.edgesAdded.length === 1 ? '' : 's'}`);
  }
  if (diff.edgesRemoved.length > 0) {
    parts.push(`${diff.edgesRemoved.length} connection${diff.edgesRemoved.length === 1 ? '' : 's'} removed`);
  }
  if (diff.expressionsAdded > 0) {
    parts.push(`${diff.expressionsAdded} expression${diff.expressionsAdded === 1 ? '' : 's'}`);
  }
  if (diff.credentialsRequired.length > 0) {
    parts.push(`needs ${diff.credentialsRequired.join(', ')}`);
  }
  return parts.length > 0 ? parts.join(' · ') : 'No visible change';
}

function overlaps(a: WorkflowNode, b: WorkflowNode): boolean {
  if (!a.position || !b.position) return false;
  return (
    Math.abs(a.position.x - b.position.x) < 60 && Math.abs(a.position.y - b.position.y) < 60
  );
}

/** True when any two nodes sit on (almost) the same spot. */
export function hasOverlap(definition: WorkflowDefinition): boolean {
  for (let i = 0; i < definition.nodes.length; i += 1) {
    for (let j = i + 1; j < definition.nodes.length; j += 1) {
      if (overlaps(definition.nodes[i], definition.nodes[j])) return true;
    }
  }
  return false;
}

/**
 * Validate, apply and lay out a list of mutations. Invalid entries are skipped
 * and reported in `errors`; the returned definition is always valid to render.
 *
 * Positions: nodes added without one are laid out hierarchically, and any
 * overlap is repaired, so generated nodes never stack.
 */
export interface WorkflowDraft {
  name: string;
  description?: string;
  definition: WorkflowDefinition;
  diff: GraphDiff;
  errors: string[];
}

/**
 * Start a fresh workflow from chat (`create_workflow`): a trigger plus any
 * mutations supplied in the same call, laid out in one step.
 */
export function createWorkflowDraft(input: {
  name: string;
  description?: string;
  trigger?: TriggerType;
  mutations?: GraphMutation[];
}): WorkflowDraft {
  const base = emptyDefinition();
  const trigger = makeTrigger(input.trigger ?? 'manual');
  trigger.name = input.trigger === 'schedule' ? 'Schedule trigger' : 'Manual trigger';
  base.triggers = [trigger];

  const outcome = applyMutations(base, input.mutations ?? []);
  return {
    name: input.name,
    description: input.description,
    definition: outcome.definition,
    diff: outcome.diff,
    errors: outcome.errors,
  };
}

export function applyMutations(
  definition: WorkflowDefinition,
  mutations: GraphMutation[],
  options: { layout?: boolean } = {},
): MutationOutcome {
  const next = clone(definition);
  const errors: string[] = [];
  const meta: { name?: string; tags?: string[] } = {};
  let addedWithoutPosition = false;

  let forceLayout = false;

  for (const mutation of mutations) {
    switch (mutation.op) {
      case 'add_node': {
        if (!mutation.type) {
          errors.push('add_node needs a node type');
          break;
        }
        const id = mutation.id ?? newId('n');
        if (next.nodes.some((node) => node.id === id)) {
          errors.push(`A node with id “${id}” already exists`);
          break;
        }
        const node = makeNode(mutation.type, mutation.position);
        node.id = id;
        if (mutation.label) node.name = mutation.label;
        if (mutation.config) node.config = { ...node.config, ...mutation.config };
        if (mutation.disabled) node.disabled = true;
        if (!mutation.position) {
          delete node.position;
          addedWithoutPosition = true;
        }
        next.nodes.push(node);
        break;
      }
      case 'update_node': {
        const node = next.nodes.find((item) => item.id === mutation.nodeId);
        if (!node) {
          errors.push(`No node “${mutation.nodeId}” to update`);
          break;
        }
        if (mutation.label) node.name = mutation.label;
        if (mutation.config) node.config = { ...node.config, ...mutation.config };
        if (mutation.credential_id !== undefined) {
          node.config = { ...node.config, credential_id: mutation.credential_id };
        }
        if (mutation.disabled !== undefined) node.disabled = mutation.disabled;
        break;
      }
      case 'remove_node': {
        const exists = next.nodes.some((node) => node.id === mutation.nodeId);
        if (!exists) {
          errors.push(`No node “${mutation.nodeId}” to remove`);
          break;
        }
        next.nodes = next.nodes.filter((node) => node.id !== mutation.nodeId);
        next.edges = next.edges.filter(
          (edge) => edge.from !== mutation.nodeId && edge.to !== mutation.nodeId,
        );
        break;
      }
      case 'connect_nodes': {
        const ids = new Set([
          ...next.triggers.map((trigger) => trigger.id),
          ...next.nodes.map((node) => node.id),
        ]);
        if (
          !ids.has(mutation.sourceNodeId) ||
          !ids.has(mutation.targetNodeId) ||
          mutation.sourceNodeId === mutation.targetNodeId
        ) {
          errors.push(
            `Cannot connect “${mutation.sourceNodeId}” → “${mutation.targetNodeId}”`,
          );
          break;
        }
        const sourcePort = mutation.sourcePort ?? 'out';
        const targetPort = mutation.targetPort ?? 'in';
        const duplicate = next.edges.some(
          (edge) =>
            edge.from === mutation.sourceNodeId &&
            edge.to === mutation.targetNodeId &&
            edgeSourcePort(edge) === sourcePort &&
            edgeTargetPort(edge) === targetPort,
        );
        if (duplicate) break;
        const edge = makeEdge(
          mutation.sourceNodeId,
          mutation.targetNodeId,
          sourcePort,
          targetPort,
        );
        if (mutation.label) edge.label = mutation.label;
        next.edges.push(edge);
        break;
      }
      case 'disconnect_nodes': {
        const before = next.edges.length;
        next.edges = next.edges.filter((edge) => {
          if (mutation.edgeId) return edge.id !== mutation.edgeId;
          return !(
            (!mutation.sourceNodeId || edge.from === mutation.sourceNodeId) &&
            (!mutation.targetNodeId || edge.to === mutation.targetNodeId)
          );
        });
        if (next.edges.length === before) errors.push('No matching connection to remove');
        break;
      }
      case 'set_workflow_meta': {
        if (mutation.name) meta.name = mutation.name;
        if (mutation.tags) meta.tags = mutation.tags;
        if (mutation.settings) {
          next.settings = { ...next.settings, ...mutation.settings };
        }
        break;
      }
      case 'auto_layout_graph': {
        forceLayout = true;
        break;
      }
      case 'import_curl': {
        const generated = curlToMutation(mutation.curl);
        if (!generated) {
          errors.push('Could not read that cURL command');
          break;
        }
        const id = mutation.nodeId ?? newId('n');
        if (next.nodes.some((node) => node.id === id)) {
          errors.push(`A node with id “${id}” already exists`);
          break;
        }
        const node = makeNode(generated.type);
        node.id = id;
        node.name = mutation.label ?? generated.label ?? node.name;
        node.config = { ...node.config, ...(generated.config ?? {}) };
        delete node.position;
        addedWithoutPosition = true;
        next.nodes.push(node);
        break;
      }
      case 'apply_node_fix': {
        const node = next.nodes.find((item) => item.id === mutation.nodeId);
        if (!node) {
          errors.push(`No node “${mutation.nodeId}” to fix`);
          break;
        }
        if (mutation.label) node.name = mutation.label;
        if (mutation.config) node.config = { ...node.config, ...mutation.config };
        if (mutation.disabled !== undefined) node.disabled = mutation.disabled;
        break;
      }
      default:
        errors.push(`Unknown mutation “${(mutation as { op: string }).op}”`);
    }
  }

  const shouldLayout =
    options.layout !== false && (forceLayout || addedWithoutPosition || hasOverlap(next));
  const laidOut = shouldLayout ? layoutWorkflow(next) : next;

  return {
    definition: laidOut,
    diff: summarizeDiff(definition, laidOut),
    errors,
    meta,
  };
}
