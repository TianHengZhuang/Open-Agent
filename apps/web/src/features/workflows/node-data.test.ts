import { emptyDefinition } from './types';
import type { WorkflowDefinition } from './types';
import {
  itemCount,
  itemJson,
  resolveNodeInputs,
  resolveNodeOutput,
  toItems,
} from './node-data';

function graph(): WorkflowDefinition {
  return {
    ...emptyDefinition(),
    triggers: [{ id: 'trg_1', type: 'manual', name: 'Start', config: {} }],
    nodes: [
      { id: 'n_1', type: 'tool', name: 'Fetch', config: {} },
      { id: 'n_2', type: 'set', name: 'Shape', config: {} },
    ],
    edges: [
      { id: 'e_1', from: 'trg_1', to: 'n_1', condition: { when: 'always' } },
      { id: 'e_2', from: 'n_1', to: 'n_2', label: 'rows', condition: { when: 'always' } },
    ],
  };
}

describe('toItems', () => {
  it('treats empty output as no items', () => {
    expect(toItems(undefined)).toEqual([]);
    expect(toItems(null)).toEqual([]);
    expect(toItems({})).toEqual([]);
  });

  it('unwraps a data node item list and keeps object outputs as one item', () => {
    expect(toItems({ items: [{ a: 1 }, { a: 2 }] }).map((i) => i.index)).toEqual([0, 1]);
    expect(toItems({ items: [{ a: 1 }, { a: 2 }] })[1].json).toEqual({ a: 2 });
    expect(toItems({ ok: true })).toEqual([{ index: 0, json: { ok: true } }]);
  });

  it('treats a bare array as an item list', () => {
    expect(toItems(['a', 'b']).map((i) => i.json)).toEqual(['a', 'b']);
    expect(itemCount(['a', 'b'])).toBe(2);
  });

  it('counts items for the canvas badge', () => {
    expect(itemCount({ items: [1, 2, 3], count: 3 })).toBe(3);
    expect(itemCount({ ok: true })).toBe(1);
    expect(itemCount({})).toBe(0);
  });

  it('serialises an item as pretty JSON for copy', () => {
    expect(itemJson({ index: 0, json: { a: 1 } })).toBe('{\n  "a": 1\n}');
  });
});

describe('resolveNodeInputs', () => {
  it('prefers the inputs the engine recorded, mapped to edge labels', () => {
    const sources = resolveNodeInputs({
      definition: graph(),
      nodeId: 'n_2',
      run: { node_id: 'n_2', inputs: { rows: { items: [{ id: 7 }] } } },
    });
    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({ label: 'rows', from_id: 'n_1', source: 'run' });
    expect(sources[0].items[0].json).toEqual({ id: 7 });
  });

  it('falls back to the upstream run output when the node has not run', () => {
    const sources = resolveNodeInputs({
      definition: graph(),
      nodeId: 'n_2',
      run: null,
      runs: [
        { node_id: 'trg_1', outputs: { start: true } },
        { node_id: 'n_1', node_name: 'Fetch', outputs: { items: [1, 2] } },
      ],
    });
    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({ label: 'rows', from_id: 'n_1', source: 'run' });
    expect(sources[0].items).toHaveLength(2);
  });

  it('shows pinned upstream output when nothing has run', () => {
    const sources = resolveNodeInputs({
      definition: graph(),
      nodeId: 'n_2',
      pinned: {
        n_1: { pinned_at: '2026-11-22T10:00:00Z', outputs: { items: [{ pinned: true }] } },
      },
    });
    expect(sources[0]).toMatchObject({ source: 'pinned', pinned_at: '2026-11-22T10:00:00Z' });
    expect(sources[0].items[0].json).toEqual({ pinned: true });
  });

  it('reports an empty source for unrun, unpinned upstreams', () => {
    expect(resolveNodeInputs({ definition: graph(), nodeId: 'n_2' })[0]).toMatchObject({
      source: 'none',
      items: [],
    });
    // A node fed only by the trigger still names it, so the pane says why it is empty.
    expect(resolveNodeInputs({ definition: graph(), nodeId: 'n_1' })[0]).toMatchObject({
      label: 'Start',
      from_id: 'trg_1',
      source: 'none',
    });
    // No incoming edges at all: nothing to show.
    expect(
      resolveNodeInputs({
        definition: { ...graph(), edges: [] },
        nodeId: 'n_2',
      }),
    ).toEqual([]);
  });
});

describe('resolveNodeOutput', () => {
  const run = {
    node_id: 'n_1',
    status: 'succeeded',
    outputs: { items: [{ id: 1 }] },
    duration_ms: 42,
  };

  it('uses the run output when nothing is pinned', () => {
    const out = resolveNodeOutput({ run });
    expect(out).toMatchObject({ source: 'run', status: 'succeeded', duration_ms: 42, newer_run: false });
    expect(out.items).toHaveLength(1);
  });

  it('returns an empty pane before the node has run', () => {
    expect(resolveNodeOutput({})).toMatchObject({ source: 'none', items: [] });
  });

  it('lets pinned data win over a run, flagging the newer run', () => {
    const out = resolveNodeOutput({
      run,
      pinned: { pinned_at: '2026-11-22T09:00:00Z', outputs: { items: [{ id: 2 }, { id: 3 }] } },
    });
    expect(out.source).toBe('pinned');
    expect(out.items).toHaveLength(2);
    expect(out.newer_run).toBe(true);
    expect(out.pinned_at).toBe('2026-11-22T09:00:00Z');
  });

  it('surfaces run errors for the pane banner', () => {
    const out = resolveNodeOutput({
      run: { node_id: 'n_1', status: 'failed', outputs: {}, error: 'boom', reason: 'UPSTREAM' },
    });
    expect(out.error).toBe('boom');
    expect(out.reason).toBe('UPSTREAM');
    expect(out.items).toEqual([]);
  });
});
