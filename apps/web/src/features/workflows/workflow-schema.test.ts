import { describe, expect, it } from 'vitest';

import { workflowDefinitionSchema } from './schema';
import { migrateDefinitionSchema } from './serialize';
import { SCHEMA_VERSION } from './types';
import { validateWorkflowDefinition } from './validate';
import type { WorkflowDefinition } from './types';

function definition(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    schema_version: SCHEMA_VERSION,
    triggers: [{ id: 'trg', type: 'manual', name: 'Start', config: {} }],
    nodes: [{ id: 'n1', type: 'set', name: 'Set', config: { values: { ok: true } } }],
    edges: [{ id: 'e1', from: 'trg', to: 'n1' }],
    variables: [],
    settings: {},
    ...overrides,
  };
}

describe('workflow schema 1.1', () => {
  it('adds 1.1 defaults when importing a 1.0 file', () => {
    const raw = definition({ schema_version: '1.0' });
    const migrated = migrateDefinitionSchema('1.0', raw);
    expect(migrated.ok).toBe(true);
    const upgraded = migrated.definition as WorkflowDefinition;
    expect(upgraded.schema_version).toBe('1.1');
    expect(upgraded.static_data).toEqual({});
    expect(upgraded.metadata).toEqual({});
    expect(upgraded.nodes).toEqual(raw.nodes);
  });

  it('rejects unknown versions with a clear error', () => {
    const migrated = migrateDefinitionSchema('9.9', definition());
    expect(migrated.ok).toBe(false);
    expect(migrated.error).toMatch(/Unsupported workflow schemaVersion/);
  });

  it('accepts the additive fields through the import schema', () => {
    const parsed = workflowDefinitionSchema.safeParse({
      ...definition(),
      error_workflow_id: '11111111-1111-1111-1111-111111111111',
      static_data: { brand: 'OpenAgent' },
      metadata: { author: 'test' },
    });
    expect(parsed.success).toBe(true);
  });

  it('validates registry-listed node types the old local list rejected', () => {
    const connector = definition({
      nodes: [{
        id: 'n1',
        type: 'connector_action',
        name: 'Create issue',
        config: { action_id: 'github.create_issue' },
      }],
    });
    const result = validateWorkflowDefinition(connector);
    expect(result.errors.some((e) => e.code === 'UNKNOWN_NODE_TYPE')).toBe(false);

    const unknown = definition({
      nodes: [{ id: 'n1', type: 'teleport' as never, name: 'Nope', config: {} }],
    });
    expect(
      validateWorkflowDefinition(unknown).errors.some((e) => e.code === 'UNKNOWN_NODE_TYPE'),
    ).toBe(true);
  });

  it('enforces pinned node versions', () => {
    const future = definition({
      nodes: [{
        id: 'n1',
        type: 'set',
        name: 'Set',
        type_version: 99,
        config: { values: { ok: true } },
      }],
    });
    const result = validateWorkflowDefinition(future);
    expect(result.errors.some((e) => e.code === 'NODE_VERSION_UNSUPPORTED')).toBe(true);
  });

  it('validates 1.1 metadata and execution settings', () => {
    const bad = definition({
      settings: { execution_timeout_seconds: 0 },
      metadata: 'nope' as never,
    });
    const result = validateWorkflowDefinition(bad);
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.field === 'metadata')).toBe(true);
    expect(
      result.errors.some((e) => e.field === 'settings.execution_timeout_seconds'),
    ).toBe(true);
  });
});
