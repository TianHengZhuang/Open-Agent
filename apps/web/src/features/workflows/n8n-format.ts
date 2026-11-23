// n8n workflow JSON compatibility. Imports the common node set and exports a
// graph back out; unmapped nodes are kept as transform nodes with their
// original n8n type/parameters, and reported, so nothing is silently dropped.

import { makeEdge, makeNode, makeTrigger, newId } from './factory';
import { registryOutputPorts, triggerOutputPorts } from './registry';
import {
  SCHEMA_VERSION,
  type TriggerType,
  type WorkflowDefinition,
  type WorkflowNodeType,
} from './types';

export interface N8nNode {
  id?: string;
  name: string;
  type: string;
  typeVersion?: number;
  position?: [number, number];
  parameters?: Record<string, unknown>;
  disabled?: boolean;
  credentials?: Record<string, unknown>;
  notes?: string;
}

export interface N8nWorkflow {
  name?: string;
  nodes: N8nNode[];
  connections?: Record<string, { main?: Array<Array<{ node: string; type?: string; index?: number }>> }>;
  settings?: Record<string, unknown>;
  pinData?: Record<string, Array<{ json?: Record<string, unknown> }>>;
  active?: boolean;
  tags?: Array<{ name?: string }> | string[];
}

const TRIGGER_TYPES: Record<string, TriggerType> = {
  'n8n-nodes-base.manualTrigger': 'manual',
  'n8n-nodes-base.start': 'manual',
  'n8n-nodes-base.webhook': 'webhook',
  'n8n-nodes-base.scheduleTrigger': 'schedule',
  'n8n-nodes-base.cron': 'schedule',
  'n8n-nodes-base.chatTrigger': 'chat',
  'n8n-nodes-base.errorTrigger': 'event',
};

const NODE_TYPES: Record<string, WorkflowNodeType> = {
  'n8n-nodes-base.set': 'set',
  'n8n-nodes-base.if': 'condition',
  'n8n-nodes-base.switch': 'switch',
  'n8n-nodes-base.merge': 'merge',
  'n8n-nodes-base.splitOut': 'split_out',
  'n8n-nodes-base.splitInBatches': 'loop',
  'n8n-nodes-base.wait': 'delay',
  'n8n-nodes-base.httpRequest': 'webhook',
  'n8n-nodes-base.filter': 'filter',
  'n8n-nodes-base.limit': 'limit',
  'n8n-nodes-base.sort': 'sort',
  'n8n-nodes-base.removeDuplicates': 'remove_duplicates',
  'n8n-nodes-base.executeWorkflow': 'subworkflow',
  'n8n-nodes-base.code': 'transform',
  'n8n-nodes-base.noOp': 'set',
  '@n8n/n8n-nodes-langchain.agent': 'agent',
  '@n8n/n8n-nodes-langchain.chainLlm': 'prompt',
};

const EXPORTED_TYPES: Partial<Record<WorkflowNodeType, string>> = {
  set: 'n8n-nodes-base.set',
  condition: 'n8n-nodes-base.if',
  switch: 'n8n-nodes-base.switch',
  merge: 'n8n-nodes-base.merge',
  split_out: 'n8n-nodes-base.splitOut',
  loop: 'n8n-nodes-base.splitInBatches',
  delay: 'n8n-nodes-base.wait',
  webhook: 'n8n-nodes-base.httpRequest',
  filter: 'n8n-nodes-base.filter',
  limit: 'n8n-nodes-base.limit',
  sort: 'n8n-nodes-base.sort',
  remove_duplicates: 'n8n-nodes-base.removeDuplicates',
  subworkflow: 'n8n-nodes-base.executeWorkflow',
  transform: 'n8n-nodes-base.code',
  agent: '@n8n/n8n-nodes-langchain.agent',
  prompt: '@n8n/n8n-nodes-langchain.chainLlm',
};

const EXPORTED_TRIGGERS: Record<TriggerType, string> = {
  manual: 'n8n-nodes-base.manualTrigger',
  webhook: 'n8n-nodes-base.webhook',
  schedule: 'n8n-nodes-base.scheduleTrigger',
  chat: 'n8n-nodes-base.chatTrigger',
  event: 'n8n-nodes-base.errorTrigger',
};

/** Sub-node types (models, memory, tools, parsers) that have no OpenAgent peer. */
function isSubNode(type: string): boolean {
  return /(lmChat|memory|embeddings|vectorStore|tool[A-Z]|outputParser|documentLoader|textSplitter)/.test(
    type,
  );
}

export function isN8nWorkflow(value: unknown): value is N8nWorkflow {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<N8nWorkflow>;
  return Array.isArray(candidate.nodes) && typeof candidate.connections === 'object';
}

export interface N8nImport {
  ok: boolean;
  definition?: WorkflowDefinition;
  name?: string;
  tags?: string[];
  warnings: string[];
  error?: string;
}

function cronOf(node: N8nNode): string {
  const parameters = node.parameters ?? {};
  const rule = (parameters.rule ?? {}) as { interval?: Array<{ cronExpression?: string }> };
  return (
    String(
      rule.interval?.[0]?.cronExpression ??
        parameters.cronExpression ??
        parameters.expression ??
        '0 9 * * *',
    ) || '0 9 * * *'
  );
}

function triggerConfig(type: TriggerType, node: N8nNode): Record<string, unknown> {
  const parameters = node.parameters ?? {};
  if (type === 'webhook') {
    return {
      path: String(parameters.path ?? 'webhook'),
      method: String(parameters.httpMethod ?? parameters.method ?? 'POST').toUpperCase(),
      ...(parameters.authentication ? { auth: parameters.authentication } : {}),
    };
  }
  if (type === 'schedule') return { cron: cronOf(node) };
  return { ...parameters };
}

function nodeConfig(type: WorkflowNodeType, node: N8nNode): Record<string, unknown> {
  const parameters = node.parameters ?? {};
  const config: Record<string, unknown> = { n8n_parameters: parameters };
  if (type === 'webhook') {
    config.url = parameters.url ?? '';
    config.method = String(parameters.method ?? 'POST').toUpperCase();
  }
  if (type === 'delay') {
    config.duration_seconds = parameters.amount ?? parameters.duration ?? 1;
  }
  if (parameters.credentials && typeof parameters.credentials === 'object') {
    config.n8n_credentials = parameters.credentials;
  }
  return config;
}

/**
 * Convert an n8n workflow into an OpenAgent definition. Unknown action nodes
 * become `transform` nodes (their n8n type and parameters preserved under
 * `config.n8n_parameters`) and sub-nodes are skipped with a warning.
 */
export function fromN8nWorkflow(workflow: N8nWorkflow): N8nImport {
  const warnings: string[] = [];
  const definition: WorkflowDefinition = {
    schema_version: SCHEMA_VERSION,
    triggers: [],
    nodes: [],
    edges: [],
    variables: [],
    settings: { ...(workflow.settings ?? {}) },
  };

  const idByName = new Map<string, string>();
  const usedNames = new Set<string>();

  for (const node of workflow.nodes) {
    if (!node?.name || !node.type) continue;
    if (node.type === 'n8n-nodes-base.stickyNote') continue;
    if (isSubNode(node.type)) {
      warnings.push(`Skipped sub-node “${node.name}” (${node.type}).`);
      continue;
    }

    let name = node.name;
    let suffix = 2;
    while (usedNames.has(name)) {
      name = `${node.name} (${suffix})`;
      suffix += 1;
    }
    usedNames.add(name);

    const position =
      Array.isArray(node.position) && node.position.length === 2
        ? { x: node.position[0], y: node.position[1] }
        : undefined;

    const trigger = TRIGGER_TYPES[node.type];
    if (trigger) {
      const created = makeTrigger(trigger, position);
      created.id = newId('trg');
      created.name = name;
      created.config = triggerConfig(trigger, node);
      if (position) created.position = position;
      definition.triggers.push(created);
      idByName.set(node.name, created.id);
      continue;
    }

    const mapped = NODE_TYPES[node.type];
    if (!mapped) warnings.push(`“${name}” (${node.type}) mapped to a transform node.`);
    const type: WorkflowNodeType = mapped ?? 'transform';
    const created = makeNode(type, position);
    created.id = newId('n');
    created.name = name;
    created.config = { ...created.config, ...nodeConfig(type, node) };
    if (node.disabled) created.disabled = true;
    if (node.notes) created.notes = node.notes;
    definition.nodes.push(created);
    idByName.set(node.name, created.id);
  }

  const createdById = new Map(definition.nodes.map((node) => [node.id, node]));
  for (const [sourceName, outputs] of Object.entries(workflow.connections ?? {})) {
    const from = idByName.get(sourceName);
    if (!from) continue;
    const sourceNode = createdById.get(from);
    const sourcePorts = sourceNode
      ? registryOutputPorts(String(sourceNode.type), sourceNode.config)
      : [];
    const main = outputs?.main ?? [];
    main.forEach((branches, port) => {
      const portName = sourcePorts[port]?.name;
      for (const branch of branches ?? []) {
        const to = idByName.get(branch.node);
        if (!to) continue;
        const edge = makeEdge(from, to, portName && portName !== 'out' ? portName : undefined);
        if (portName && portName !== 'out') {
          edge.from_port = portName;
          edge.label = portName;
        } else if (port > 0) {
          edge.label = String(port);
        }
        definition.edges.push(edge);
      }
    });
  }

  for (const [name, items] of Object.entries(workflow.pinData ?? {})) {
    const id = idByName.get(name);
    if (!id || !Array.isArray(items)) continue;
    const pinned = { ...(definition.settings.pinned_data ?? {}) };
    pinned[id] = {
      pinned_at: new Date().toISOString(),
      outputs: { items: items.map((item) => item.json ?? {}) },
    };
    definition.settings.pinned_data = pinned;
  }

  return {
    ok: true,
    definition,
    name: workflow.name,
    tags: Array.isArray(workflow.tags)
      ? workflow.tags
          .map((tag) => (typeof tag === 'string' ? tag : tag?.name))
          .filter((tag): tag is string => !!tag)
      : [],
    warnings,
  };
}

/** Export an OpenAgent graph as n8n workflow JSON (common node subset). */
export function toN8nWorkflow(
  definition: WorkflowDefinition,
  meta: { name?: string; tags?: string[] } = {},
): N8nWorkflow {
  const nameById = new Map<string, string>();
  const usedNames = new Set<string>();
  const unique = (value: string) => {
    let name = value;
    let suffix = 2;
    while (usedNames.has(name)) {
      name = `${value} (${suffix})`;
      suffix += 1;
    }
    usedNames.add(name);
    return name;
  };

  const nodes: N8nNode[] = [];
  for (const trigger of definition.triggers) {
    const name = unique(trigger.name);
    nameById.set(trigger.id, name);
    nodes.push({
      id: trigger.id,
      name,
      type: EXPORTED_TRIGGERS[trigger.type] ?? 'n8n-nodes-base.manualTrigger',
      typeVersion: 1,
      position: [trigger.position?.x ?? 0, trigger.position?.y ?? 0],
      parameters:
        trigger.type === 'webhook'
          ? {
              path: trigger.config.path ?? 'webhook',
              httpMethod: trigger.config.method ?? 'POST',
              ...(trigger.config.auth ? { authentication: trigger.config.auth } : {}),
            }
          : trigger.type === 'schedule'
            ? { rule: { interval: [{ field: 'cron', cronExpression: trigger.config.cron }] } }
            : { ...trigger.config },
    });
  }

  for (const node of definition.nodes) {
    const name = unique(node.name);
    nameById.set(node.id, name);
    const original = node.config.n8n_parameters;
    nodes.push({
      id: node.id,
      name,
      type: EXPORTED_TYPES[node.type] ?? 'n8n-nodes-base.noOp',
      typeVersion: 1,
      position: [node.position?.x ?? 0, node.position?.y ?? 0],
      parameters:
        original && typeof original === 'object'
          ? (original as Record<string, unknown>)
          : Object.fromEntries(
              Object.entries(node.config).filter(
                ([key]) => !['n8n_parameters', 'n8n_credentials'].includes(key),
              ),
            ),
      ...(node.disabled ? { disabled: true } : {}),
      ...(node.notes ? { notes: node.notes } : {}),
    });
  }

  const connections: N8nWorkflow['connections'] = {};
  const ports = new Map<string, Map<number, string[]>>();
  for (const edge of definition.edges) {
    const from = nameById.get(edge.from);
    const to = nameById.get(edge.to);
    if (!from || !to) continue;
    const sourceNode = definition.nodes.find((node) => node.id === edge.from);
    const sourceTrigger = definition.triggers.find((trigger) => trigger.id === edge.from);
    const sourcePorts = sourceNode
      ? registryOutputPorts(String(sourceNode.type), sourceNode.config)
      : sourceTrigger
        ? triggerOutputPorts(sourceTrigger.type)
        : [];
    const portName = edge.from_port ?? edge.label;
    const named = portName ? sourcePorts.findIndex((p) => p.name === portName) : -1;
    const numeric = Number(portName);
    const port = named >= 0 ? named : Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
    const byPort = ports.get(from) ?? new Map<number, string[]>();
    byPort.set(port, [...(byPort.get(port) ?? []), to]);
    ports.set(from, byPort);
  }
  for (const [from, byPort] of ports) {
    const maxPort = Math.max(...byPort.keys());
    const main = Array.from({ length: maxPort + 1 }, (_, port) =>
      (byPort.get(port) ?? []).map((node) => ({ node, type: 'main', index: 0 })),
    );
    connections[from] = { main };
  }

  const pinned = (definition.settings.pinned_data ?? {}) as Record<
    string,
    { outputs?: { items?: unknown } }
  >;
  const pinData: N8nWorkflow['pinData'] = {};
  for (const [id, datum] of Object.entries(pinned)) {
    const nodeName = nameById.get(id) ?? nameById.get(id.replace(/^n_/, ''));
    const items = datum?.outputs?.items;
    if (nodeName && Array.isArray(items)) {
      pinData[nodeName] = items.map((json) => ({
        json: typeof json === 'object' && json !== null ? (json as Record<string, unknown>) : { value: json },
      }));
    }
  }

  return {
    name: meta.name ?? 'OpenAgent workflow',
    nodes,
    connections,
    ...(meta.tags && meta.tags.length > 0 ? { tags: meta.tags.map((name) => ({ name })) } : {}),
    ...(Object.keys(pinData).length > 0 ? { pinData } : {}),
    settings: definition.settings as unknown as Record<string, unknown>,
  };
}

export function exportN8nJson(
  definition: WorkflowDefinition,
  meta: { name?: string; tags?: string[] } = {},
): string {
  return JSON.stringify(toN8nWorkflow(definition, meta), null, 2);
}
