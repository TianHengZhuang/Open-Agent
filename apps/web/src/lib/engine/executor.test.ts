import { describe, expect, it } from 'vitest';
import { executeWorkflowPreview } from './executor';
import type { WorkflowDefinition, WorkflowNode, WorkflowNodeType } from '@/features/workflows/types';
import { emptyDefinition } from '@/features/workflows/types';

function node(
  id: string,
  type: WorkflowNodeType,
  name: string,
  config: Record<string, unknown> = {},
): WorkflowNode {
  return { id, type, name, config };
}

function definition(
  nodes: WorkflowNode[],
  edges: Array<{ id: string; from: string; to: string; label?: string }>,
): WorkflowDefinition {
  return {
    ...emptyDefinition(),
    triggers: [{ id: 'trg', type: 'manual', name: 'Start', config: {} }],
    nodes,
    edges,
  };
}

const threeItems = [
  { json: { name: 'A', n: 1, ok: true, tags: ['x', 'y'] } },
  { json: { name: 'B', n: 2, ok: false, tags: [] } },
  { json: { name: 'C', n: 3, ok: true, tags: ['z'] } },
];

describe('executeWorkflowPreview', () => {
  it('runs a set node once per item and tracks pairedItem', () => {
    const graph = definition(
      [node('n_set', 'set', 'Set', { values: { greeting: 'Hi {{ $json.name }}' } })],
      [{ id: 'e1', from: 'trg', to: 'n_set' }],
    );

    const result = executeWorkflowPreview(graph, { items: threeItems });
    expect(result.errors).toEqual([]);
    expect(result.runs[0].status).toBe('succeeded');
    expect(result.outputs.Set).toHaveLength(3);
    expect(result.outputs.Set.map((item) => item.json.greeting)).toEqual([
      'Hi A',
      'Hi B',
      'Hi C',
    ]);
    expect(result.outputs.Set.map((item) => item.pairedItem)).toEqual([0, 1, 2]);
  });

  it('honours execute_once', () => {
    const graph = definition(
      [node('n_set', 'set', 'Set', { values: { once: true }, execute_once: true })],
      [{ id: 'e1', from: 'trg', to: 'n_set' }],
    );

    const result = executeWorkflowPreview(graph, { items: threeItems });
    expect(result.outputs.Set).toHaveLength(1);
  });

  it('routes condition branches to labelled edges', () => {
    const graph = definition(
      [
        node('n_if', 'condition', 'IF', { expression: '$json.n > 1' }),
        node('n_high', 'set', 'High', { values: { lane: 'high' } }),
        node('n_low', 'set', 'Low', { values: { lane: 'low' } }),
      ],
      [
        { id: 'e1', from: 'trg', to: 'n_if' },
        { id: 'e2', from: 'n_if', to: 'n_high' },
        { id: 'e3', from: 'n_if', to: 'n_low', label: 'false' },
      ],
    );

    const result = executeWorkflowPreview(graph, { items: threeItems });
    expect(result.outputs.High.map((item) => item.json.name)).toEqual(['B', 'C']);
    expect(result.outputs.Low.map((item) => item.json.name)).toEqual(['A']);
  });

  it('filters, limits and splits items', () => {
    const graph = definition(
      [
        node('n_filter', 'filter', 'Only ok', { predicate: '$json.ok' }),
        node('n_limit', 'limit', 'First one', { max_items: 1, keep: 'first' }),
        node('n_split', 'split_out', 'Tags', { field: 'tags' }),
      ],
      [
        { id: 'e1', from: 'trg', to: 'n_filter' },
        { id: 'e2', from: 'n_filter', to: 'n_limit' },
        { id: 'e3', from: 'n_limit', to: 'n_split' },
      ],
    );

    const result = executeWorkflowPreview(graph, { items: threeItems });
    expect(result.errors).toEqual([]);
    expect(result.outputs['First one']).toHaveLength(1);
    expect(result.outputs.Tags.map((item) => item.json.value)).toEqual(['x', 'y']);
  });

  it('sorts and de-duplicates items', () => {
    const graph = definition(
      [
        node('n_sort', 'sort', 'Sort', { field: 'name', direction: 'desc' }),
        node('n_unique', 'remove_duplicates', 'Unique', { field: 'ok' }),
      ],
      [
        { id: 'e1', from: 'trg', to: 'n_sort' },
        { id: 'e2', from: 'n_sort', to: 'n_unique' },
      ],
    );

    const result = executeWorkflowPreview(graph, { items: threeItems });
    expect(result.outputs.Sort.map((item) => item.json.name)).toEqual(['C', 'B', 'A']);
    expect(result.outputs.Unique).toHaveLength(2);
  });

  it('reports side-effecting nodes as not simulated', () => {
    const graph = definition(
      [node('n_http', 'webhook', 'HTTP Request', { url: 'https://example.com' })],
      [{ id: 'e1', from: 'trg', to: 'n_http' }],
    );

    const result = executeWorkflowPreview(graph);
    expect(result.runs[0].status).toBe('failed');
    expect(result.errors[0]).toContain('not simulated');
  });
});
