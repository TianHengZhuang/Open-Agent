import { LAYOUT_DEFAULTS, layoutWorkflow } from './auto-layout';
import { emptyDefinition } from './types';
import type { WorkflowDefinition } from './types';

function diamond(): WorkflowDefinition {
  return {
    ...emptyDefinition(),
    triggers: [{ id: 'trg', type: 'manual', name: 'Start', config: {}, position: { x: 0, y: 0 } }],
    nodes: [
      { id: 'n_a', type: 'delay', name: 'A', config: {}, position: { x: 0, y: 900 } },
      { id: 'n_b', type: 'delay', name: 'B', config: {}, position: { x: 0, y: 900 } },
      { id: 'n_c', type: 'delay', name: 'C', config: {}, position: { x: 0, y: 900 } },
      { id: 'n_orphan', type: 'delay', name: 'Orphan', config: {} },
    ],
    edges: [
      { id: 'e1', from: 'trg', to: 'n_a', condition: { when: 'always' } },
      { id: 'e2', from: 'trg', to: 'n_b', condition: { when: 'always' } },
      { id: 'e3', from: 'n_a', to: 'n_c', condition: { when: 'always' } },
      { id: 'e4', from: 'n_b', to: 'n_c', condition: { when: 'always' } },
    ],
  };
}

function positionOf(definition: WorkflowDefinition, id: string) {
  const item =
    definition.triggers.find((trigger) => trigger.id === id) ??
    definition.nodes.find((node) => node.id === id);
  return item?.position!;
}

describe('layoutWorkflow', () => {
  it('puts triggers first and each node one column past its deepest parent', () => {
    const layout = layoutWorkflow(diamond());
    const step = LAYOUT_DEFAULTS.nodeWidth + LAYOUT_DEFAULTS.columnGap;

    expect(positionOf(layout, 'trg').x).toBe(LAYOUT_DEFAULTS.padding);
    expect(positionOf(layout, 'n_a').x).toBe(LAYOUT_DEFAULTS.padding + step);
    expect(positionOf(layout, 'n_b').x).toBe(LAYOUT_DEFAULTS.padding + step);
    expect(positionOf(layout, 'n_c').x).toBe(LAYOUT_DEFAULTS.padding + step * 2);
    expect(positionOf(layout, 'n_orphan').x).toBe(LAYOUT_DEFAULTS.padding);
  });

  it('never overlaps items in the same column and keeps a deterministic order', () => {
    const layout = layoutWorkflow(diamond());
    const rows = LAYOUT_DEFAULTS.nodeHeight + LAYOUT_DEFAULTS.rowGap;

    expect(positionOf(layout, 'n_a').y).toBe(LAYOUT_DEFAULTS.padding);
    expect(positionOf(layout, 'n_b').y).toBe(LAYOUT_DEFAULTS.padding + rows);
    expect(positionOf(layout, 'n_orphan').y).not.toBe(positionOf(layout, 'trg').y);

    const again = layoutWorkflow(diamond());
    expect(again).toEqual(layout);
  });

  it('does not mutate the input definition', () => {
    const original = diamond();
    const snapshot = JSON.parse(JSON.stringify(original));

    layoutWorkflow(original);

    expect(original).toEqual(snapshot);
  });

  it('preserves edges, settings, disabled state and sticky notes', () => {
    const definition = {
      ...diamond(),
      nodes: diamond().nodes.map((node) =>
        node.id === 'n_c' ? { ...node, disabled: true, config: { duration_seconds: 3 } } : node,
      ),
      settings: {
        sticky_notes: [{ id: 's1', x: 10, y: 20, width: 200, height: 120, color: 'yellow', text: 'hi' }],
      },
    };

    const layout = layoutWorkflow(definition);

    expect(layout.edges).toEqual(definition.edges);
    expect(layout.settings).toEqual(definition.settings);
    expect(layout.nodes.find((node) => node.id === 'n_c')).toMatchObject({
      disabled: true,
      config: { duration_seconds: 3 },
    });
  });

  it('parks a cycle in a trailing column instead of looping forever', () => {
    const definition: WorkflowDefinition = {
      ...emptyDefinition(),
      triggers: [{ id: 'trg', type: 'manual', name: 'Start', config: {} }],
      nodes: [
        { id: 'x', type: 'delay', name: 'X', config: {} },
        { id: 'y', type: 'delay', name: 'Y', config: {} },
      ],
      edges: [
        { id: 'e1', from: 'trg', to: 'x', condition: { when: 'always' } },
        { id: 'e2', from: 'x', to: 'y', condition: { when: 'always' } },
        { id: 'e3', from: 'y', to: 'x', condition: { when: 'always' } },
      ],
    };

    const layout = layoutWorkflow(definition);
    const step = LAYOUT_DEFAULTS.nodeWidth + LAYOUT_DEFAULTS.columnGap;

    // x resolves via the trigger; y's parent is in the cycle with x, so both
    // stay in the same trailing column without throwing.
    expect(positionOf(layout, 'x').x).toBe(LAYOUT_DEFAULTS.padding + step);
    expect(positionOf(layout, 'y').x).toBeGreaterThanOrEqual(positionOf(layout, 'x').x);
  });

  it('honours custom spacing options', () => {
    const layout = layoutWorkflow(diamond(), { nodeWidth: 100, nodeHeight: 50, columnGap: 10, rowGap: 5, padding: 0 });
    expect(positionOf(layout, 'trg').x).toBe(0);
    expect(positionOf(layout, 'n_c').x).toBe(220);
  });
});
