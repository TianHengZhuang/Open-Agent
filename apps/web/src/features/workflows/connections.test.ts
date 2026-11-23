import { describe, expect, it } from 'vitest';

import { buildEnvelope, importDefinition } from './serialize';
import { WORKFLOW_REGISTRY } from './registry';
import type { WorkflowDefinition, WorkflowEdge } from './types';
import { validateWorkflowDefinition } from './validate';

function definition(edges: WorkflowEdge[]): WorkflowDefinition {
  return {
    schema_version: '1.1',
    triggers: [{ id: 'trg', type: 'manual', name: 'Start', config: {} }],
    nodes: [
      {
        id: 'n_transform',
        type: 'transform',
        name: 'Map',
        config: { mapping: { ok: true } },
      },
      { id: 'n_set', type: 'set', name: 'Set', config: { values: { ok: true } } },
    ],
    edges,
    variables: [],
    settings: {},
  };
}

const baseEdges: WorkflowEdge[] = [
  { id: 'e1', from: 'trg', to: 'n_transform' },
  { id: 'e2', from: 'n_transform', to: 'n_set' },
];

const codes = (d: WorkflowDefinition) =>
  validateWorkflowDefinition(d).errors.map((e) => e.code);

describe('connection contract', () => {
  it('accepts legacy edges without port metadata', () => {
    const result = validateWorkflowDefinition(definition(baseEdges));
    expect(result.valid).toBe(true);
    expect(result.errors.some((e) => e.code.startsWith('UNKNOWN_'))).toBe(false);
  });

  it('accepts typed ports, labels and clean metadata', () => {
    const result = validateWorkflowDefinition(
      definition([
        { id: 'e1', from: 'trg', to: 'n_transform', from_port: 'out', to_port: 'in' },
        {
          id: 'e2',
          from: 'n_transform',
          to: 'n_set',
          from_port: 'out',
          to_port: 'in',
          connection_type: 'main',
          metadata: { ui: 'x' },
        },
      ]),
    );
    expect(result.valid).toBe(true);
  });

  it('reports unknown source and target ports with node and edge ids', () => {
    const result = validateWorkflowDefinition(
      definition([
        { id: 'e1', from: 'trg', to: 'n_transform' },
        { id: 'e2', from: 'n_transform', to: 'n_set', from_port: 'missing' },
      ]),
    );
    const source = result.errors.find((e) => e.code === 'UNKNOWN_SOURCE_PORT');
    expect(source?.node_id).toBe('n_transform');
    expect(source?.edge_id).toBe('e2');

    const target = validateWorkflowDefinition(
      definition([
        { id: 'e1', from: 'trg', to: 'n_transform' },
        { id: 'e2', from: 'n_transform', to: 'n_set', to_port: 'missing' },
      ]),
    ).errors.find((e) => e.code === 'UNKNOWN_TARGET_PORT');
    expect(target?.node_id).toBe('n_set');
    expect(target?.edge_id).toBe('e2');
  });

  it('rejects duplicate connections on the same ports', () => {
    expect(
      codes(
        definition([
          { id: 'e1', from: 'trg', to: 'n_transform' },
          { id: 'e2', from: 'n_transform', to: 'n_set' },
          { id: 'e3', from: 'n_transform', to: 'n_set' },
        ]),
      ),
    ).toContain('DUPLICATE_EDGE');
  });

  it('allows condition branching on named outputs', () => {
    const branching = definition([
      { id: 'e0', from: 'trg', to: 'n_cond' },
      { id: 'e1', from: 'n_cond', to: 'n_set', from_port: 'true' },
      { id: 'e2', from: 'n_cond', to: 'n_set2', from_port: 'false' },
    ]);
    branching.nodes = [
      { id: 'n_cond', type: 'condition', name: 'If', config: { expression: 'ok == true' } },
      { id: 'n_set', type: 'set', name: 'Set', config: { values: { yes: true } } },
      { id: 'n_set2', type: 'set', name: 'Set 2', config: { values: { no: true } } },
    ];
    expect(validateWorkflowDefinition(branching).valid).toBe(true);
  });

  it('allows multiple inbound edges only on a multi-connection port', () => {
    const merged = definition([
      { id: 'e1', from: 'trg', to: 'n_set' },
      { id: 'e2', from: 'trg', to: 'n_set2' },
      { id: 'e3', from: 'n_set', to: 'n_merge' },
      { id: 'e4', from: 'n_set2', to: 'n_merge' },
      { id: 'e5', from: 'n_merge', to: 'n_transform' },
    ]);
    merged.nodes.push(
      { id: 'n_merge', type: 'merge', name: 'Join', config: {} },
      { id: 'n_set2', type: 'set', name: 'Set 2', config: { values: { a: 1 } } },
    );
    expect(validateWorkflowDefinition(merged).valid).toBe(true);

    const invalid = definition([
      { id: 'e1', from: 'trg', to: 'n_transform' },
      { id: 'e2', from: 'trg', to: 'n_set' },
      { id: 'e3', from: 'n_transform', to: 'n_set' },
    ]);
    expect(codes(invalid)).toContain('MULTIPLE_INBOUND_EDGES');
  });

  it('rejects incompatible port data types', () => {
    const set = WORKFLOW_REGISTRY.nodes.find((n) => n.type === 'set')!;
    const transform = WORKFLOW_REGISTRY.nodes.find((n) => n.type === 'transform')!;
    const setPorts = set.input_ports;
    const transformPorts = transform.output_ports;
    try {
      set.input_ports = [{
        name: 'in',
        label: 'Input',
        direction: 'input',
        data_types: ['number'],
        required: true,
      }];
      transform.output_ports = [{
        name: 'out',
        label: 'Output',
        direction: 'output',
        data_types: ['text'],
        multiple: true,
      }];
      const result = validateWorkflowDefinition(definition(baseEdges));
      expect(result.errors.some((e) => e.code === 'INCOMPATIBLE_PORT_TYPES')).toBe(true);
    } finally {
      set.input_ports = setPorts;
      transform.output_ports = transformPorts;
    }
  });

  it('requires every required input when another input is connected', () => {
    const merge = WORKFLOW_REGISTRY.nodes.find((n) => n.type === 'merge')!;
    const original = merge.input_ports;
    try {
      merge.input_ports = [
        { name: 'a', label: 'A', direction: 'input', data_types: ['items'], required: true },
        { name: 'b', label: 'B', direction: 'input', data_types: ['items'], required: true },
      ];
      const d = definition([
        { id: 'e1', from: 'trg', to: 'n_merge', to_port: 'a' },
        { id: 'e2', from: 'n_merge', to: 'n_set' },
      ]);
      d.nodes.push({ id: 'n_merge', type: 'merge', name: 'Join', config: {} });
      expect(codes(d)).toContain('NODE_REQUIRED_INPUT_MISSING');
    } finally {
      merge.input_ports = original;
    }
  });

  it('validates connection types instead of accepting arbitrary values', () => {
    const badType = definition([
      { id: 'e1', from: 'trg', to: 'n_transform', connection_type: 'audit' as never },
      { id: 'e2', from: 'n_transform', to: 'n_set' },
    ]);
    expect(codes(badType)).toContain('UNSUPPORTED_CONNECTION_TYPE');

    const errorRoute = definition([
      { id: 'e1', from: 'trg', to: 'n_transform' },
      { id: 'e2', from: 'n_transform', to: 'n_set', connection_type: 'error' },
    ]);
    expect(codes(errorRoute)).toContain('CONNECTION_TYPE_UNAVAILABLE');
  });

  it('preserves typed ports through export/import', () => {
    const d = definition([
      { id: 'e1', from: 'trg', to: 'n_transform' },
      {
        id: 'e2',
        from: 'n_transform',
        to: 'n_set',
        from_port: 'out',
        to_port: 'in',
        connection_type: 'main',
        metadata: { note: 'kept' },
      },
    ]);
    const imported = importDefinition(
      JSON.stringify(buildEnvelope({ name: 'Ports', slug: 'ports' }, d)),
    );
    expect(imported.ok).toBe(true);
    const edge = imported.definition!.edges[1];
    expect(edge.from_port).toBe('out');
    expect(edge.to_port).toBe('in');
    expect(edge.connection_type).toBe('main');
    expect(edge.metadata).toEqual({ note: 'kept' });
  });

  it('keeps 1.0 definitions readable without port metadata', () => {
    const legacy = definition(baseEdges);
    legacy.schema_version = '1.0';
    expect(validateWorkflowDefinition(legacy).valid).toBe(true);
  });

  it('ships typed ports for every node in the shared registry', () => {
    for (const node of WORKFLOW_REGISTRY.nodes) {
      expect(node.output_ports.length).toBeGreaterThan(0);
      if (node.has_input) expect(node.input_ports.length).toBeGreaterThan(0);
      for (const port of [...node.input_ports, ...node.output_ports]) {
        expect(['input', 'output']).toContain(port.direction);
        expect(port.data_types.length).toBeGreaterThan(0);
      }
    }
    const condition = WORKFLOW_REGISTRY.nodes.find((n) => n.type === 'condition')!;
    expect(condition.output_ports.map((p) => p.name)).toEqual(['true', 'false']);
  });
});
