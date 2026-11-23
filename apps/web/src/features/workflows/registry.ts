// Typed view over the generated node registry.
//
// The single authoring source is `node-catalog.ts`. `registry.test.ts`
// serializes that catalog into `node-registry.json` here and into the API
// package (`apps/api/src/openagent/registry/node-registry.json`), so the
// frontend, the backend and the AI planner read the same definitions.
// Regenerate with:
//   WRITE_NODE_REGISTRY=1 pnpm --filter @openagent/web test src/features/workflows/registry.test.ts

import registryJson from './node-registry.json';
import { NODE_CATALOG, TRIGGER_CATALOG, outputPortsFor, type NodeMeta } from './node-catalog';
import type { PortDataType, WorkflowNode, WorkflowNodeType } from './types';

export interface RegistryField {
  key: string;
  label: string;
  type: string;
  required?: boolean;
  sensitive?: boolean;
  placeholder?: string;
  help?: string;
  options?: { value: string; label: string }[];
  min?: number;
  max?: number;
}

export interface RegistryPort {
  name: string;
  label: string;
  direction: 'input' | 'output';
  data_types: PortDataType[];
  required?: boolean;
  multiple?: boolean;
  description?: string;
  default_value?: unknown;
  /** Switch output ports are resolved from config.routes. */
  dynamic?: boolean;
}

export interface RegistryNode {
  type: string;
  version: number;
  label: string;
  description: string;
  category: string;
  source: string;
  outputs: string[];
  has_input: boolean;
  input_ports: RegistryPort[];
  output_ports: RegistryPort[];
  fan_in?: boolean;
  terminal?: boolean;
  capabilities?: string[];
  documentation_url?: string;
  default_config: Record<string, unknown>;
  config_fields: RegistryField[];
}

export interface RegistryTrigger {
  type: string;
  version: number;
  label: string;
  description: string;
  config_fields: RegistryField[];
  output_ports: RegistryPort[];
}

export interface RegistryDocument {
  registry_version: string;
  generated_from: string;
  nodes: RegistryNode[];
  triggers: RegistryTrigger[];
}

export const WORKFLOW_REGISTRY = registryJson as RegistryDocument;

/** Registry identity is `type@version`, matching node instances. */
export function registryKey(type: string, version: number): string {
  return `${type}@${version}`;
}

export function registryLatestVersion(type: string): number | undefined {
  const versions = WORKFLOW_REGISTRY.nodes
    .filter((n) => n.type === type)
    .map((n) => n.version);
  return versions.length ? Math.max(...versions) : undefined;
}

export function registryNode(type: string, version?: number): RegistryNode | undefined {
  const candidates = WORKFLOW_REGISTRY.nodes.filter(
    (n) => n.type === type && (version === undefined || n.version === version),
  );
  return candidates.sort((a, b) => b.version - a.version)[0];
}

export function registryTrigger(type: string): RegistryTrigger | undefined {
  return WORKFLOW_REGISTRY.triggers.find((t) => t.type === type);
}

export function registryInputPorts(type: string, version?: number): RegistryPort[] {
  return [...(registryNode(type, version)?.input_ports ?? [])];
}

/** Output ports for a node instance; switch routes become named ports. */
export function registryOutputPorts(
  type: string,
  config?: Record<string, unknown>,
  version?: number,
): RegistryPort[] {
  const node = registryNode(type, version);
  if (!node) return [];
  if (type === 'switch') {
    const routes = Array.isArray(config?.routes)
      ? config.routes
          .map((r) => (r && typeof r === 'object' ? (r as { name?: unknown }).name : undefined))
          .filter((n): n is string => typeof n === 'string' && n.length > 0)
      : [];
    const names = routes.length > 0 ? [...routes, 'default'] : ['out'];
    return names.map((name) => ({
      name,
      label: name === 'default' ? 'Default' : name,
      direction: 'output',
      data_types: ['items'],
      multiple: true,
      dynamic: true,
    }));
  }
  return [...node.output_ports];
}

export function triggerOutputPorts(type: string): RegistryPort[] {
  return [...(registryTrigger(type)?.output_ports ?? [])];
}

export function defaultInputPort(ports: RegistryPort[]): RegistryPort | undefined {
  return ports.find((p) => p.required) ?? ports[0];
}

const PORT_ACCEPTS: Record<PortDataType, ReadonlySet<PortDataType>> = {
  any: new Set(['any', 'items', 'json', 'text', 'number', 'boolean', 'binary']),
  // An item stream can feed any item-shaped consumer.
  items: new Set(['items', 'json', 'text', 'number', 'boolean', 'any']),
  json: new Set(['json', 'any']),
  text: new Set(['text', 'any']),
  number: new Set(['number', 'any']),
  boolean: new Set(['boolean', 'any']),
  binary: new Set(['binary', 'any']),
};

/** True when at least one source type is accepted by the target port. */
export function arePortTypesCompatible(
  sourceTypes: readonly PortDataType[],
  targetTypes: readonly PortDataType[],
): boolean {
  if (sourceTypes.length === 0 || targetTypes.length === 0) return true;
  return sourceTypes.some((source) =>
    targetTypes.some((target) => PORT_ACCEPTS[source]?.has(target) ?? false),
  );
}

/** Human-readable port list for validation messages. */
export function describePort(port: RegistryPort): string {
  return `${port.name} (${port.data_types.join('|')})`;
}

const FIELD_TYPES = new Set([
  'text', 'textarea', 'number', 'boolean', 'select', 'keyvalue',
  'stringlist', 'expression', 'json', 'credential',
]);

/** Plain, deterministic registry entry for one catalog definition. */
export function serializeNode(meta: NodeMeta): RegistryNode {
  return {
    type: String(meta.type),
    version: meta.version,
    label: meta.label,
    description: meta.description,
    category: meta.category,
    source: meta.source,
    outputs: [...meta.outputs],
    has_input: meta.hasInput,
    input_ports: serializeInputPorts(meta),
    output_ports: serializeOutputPorts(meta),
    ...(meta.fanIn ? { fan_in: true } : {}),
    ...(meta.terminal ? { terminal: true } : {}),
    ...(meta.capabilities ? { capabilities: [...meta.capabilities] } : {}),
    ...(meta.documentationUrl ? { documentation_url: meta.documentationUrl } : {}),
    default_config: { ...meta.defaultConfig },
    config_fields: meta.configFields.map((f) => ({
      key: f.key,
      label: f.label,
      type: f.type,
      ...(f.required ? { required: true } : {}),
      ...(f.sensitive ? { sensitive: true } : {}),
      ...(f.placeholder ? { placeholder: f.placeholder } : {}),
      ...(f.help ? { help: f.help } : {}),
      ...(f.options ? { options: f.options.map((o) => ({ value: o.value, label: o.label })) } : {}),
      ...(f.min !== undefined ? { min: f.min } : {}),
      ...(f.max !== undefined ? { max: f.max } : {}),
    })),
  };
}

function basePort(port: {
  name: string;
  label: string;
  direction: 'input' | 'output';
  dataTypes: PortDataType[];
  required?: boolean;
  multiple?: boolean;
  description?: string;
  defaultValue?: unknown;
}): RegistryPort {
  return {
    name: port.name,
    label: port.label,
    direction: port.direction,
    data_types: [...port.dataTypes],
    ...(port.required ? { required: true } : {}),
    ...(port.multiple ? { multiple: true } : {}),
    ...(port.description ? { description: port.description } : {}),
    ...(port.defaultValue !== undefined ? { default_value: port.defaultValue } : {}),
  };
}

export function serializeInputPorts(meta: NodeMeta): RegistryPort[] {
  if (!meta.hasInput) return [];
  const declared = meta.inputPorts ?? [{
    name: 'in',
    label: 'Input',
    direction: 'input' as const,
    dataTypes: ['items'] as PortDataType[],
    required: true,
    multiple: Boolean(meta.fanIn),
  }];
  return declared.map(basePort);
}

export function serializeOutputPorts(meta: NodeMeta): RegistryPort[] {
  const declared = meta.outputPorts ?? meta.outputs.map((name) => ({
    name,
    label: name === 'out' ? 'Output' : name.charAt(0).toUpperCase() + name.slice(1),
    direction: 'output' as const,
    dataTypes: ['items'] as PortDataType[],
    multiple: true,
    ...(meta.dynamicOutputs ? { dynamic: true } : {}),
  }));
  return declared.map((port) => ({
    ...basePort(port),
    ...(meta.dynamicOutputs ? { dynamic: true } : {}),
  }));
}

export function buildRegistryDocument(): RegistryDocument {
  return {
    registry_version: '1.0',
    generated_from: 'apps/web/src/features/workflows/node-catalog.ts',
    nodes: NODE_CATALOG.map(serializeNode).sort((a, b) =>
      a.type === b.type ? a.version - b.version : a.type.localeCompare(b.type),
    ),
    triggers: TRIGGER_CATALOG.map((t) => ({
      type: t.type,
      version: t.version,
      label: t.label,
      description: t.description,
      output_ports: [{
        name: 'out',
        label: 'Output',
        direction: 'output' as const,
        data_types: ['items'] as PortDataType[],
        multiple: true,
      }],
      config_fields: t.configFields.map((f) => ({
        key: f.key,
        label: f.label,
        type: f.type,
        ...(f.required ? { required: true } : {}),
        ...(f.sensitive ? { sensitive: true } : {}),
        ...(f.placeholder ? { placeholder: f.placeholder } : {}),
        ...(f.help ? { help: f.help } : {}),
        ...(f.options ? { options: f.options.map((o) => ({ value: o.value, label: o.label })) } : {}),
        ...(f.min !== undefined ? { min: f.min } : {}),
        ...(f.max !== undefined ? { max: f.max } : {}),
      })),
    })).sort((a, b) => a.type.localeCompare(b.type)),
  };
}

export function serializeRegistry(doc: RegistryDocument = buildRegistryDocument()): string {
  return `${JSON.stringify(doc, null, 2)}\n`;
}

/**
 * Compare an instance's pinned node version with the registry.
 * Mirrors the backend `check_node_version` so the editor reports the same
 * issues the API will.
 */
export function checkNodeVersion(
  type: string,
  typeVersion: number | undefined,
): { error?: string; warning?: string } {
  const latest = registryLatestVersion(type);
  if (latest === undefined || typeVersion === undefined) return {};
  if (typeVersion > latest) {
    return { error: `Node type '${type}' version ${typeVersion} is newer than the registry (latest ${latest}).` };
  }
  if (typeVersion < latest) {
    return { warning: `Node type '${type}' is pinned to v${typeVersion}; v${latest} is available.` };
  }
  return {};
}

export function isKnownNodeType(type: string): type is WorkflowNodeType | string {
  return WORKFLOW_REGISTRY.nodes.some((n) => n.type === type);
}

/** Guard used by tests: every serialized field type is one the UI can render. */
export function registryFieldTypesAreValid(doc: RegistryDocument = WORKFLOW_REGISTRY): boolean {
  return [...doc.nodes, ...doc.triggers].every((entry) =>
    entry.config_fields.every((f) => FIELD_TYPES.has(f.type)),
  );
}
