import * as React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { NodeDetailView } from './NodeDetailView';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import { emptyDefinition } from '@/features/workflows/types';
import type { WorkflowDefinition } from '@/features/workflows/types';

function definition(): WorkflowDefinition {
  return {
    ...emptyDefinition(),
    triggers: [{ id: 'trg_1', type: 'manual', name: 'Start', config: {} }],
    nodes: [
      { id: 'n_1', type: 'delay', name: 'Wait', config: { duration_seconds: 5 } },
      { id: 'n_2', type: 'set', name: 'Shape', config: {} },
    ],
    edges: [
      { id: 'e_1', from: 'trg_1', to: 'n_1', condition: { when: 'always' } },
      { id: 'e_2', from: 'n_1', to: 'n_2', condition: { when: 'always' } },
    ],
  };
}

function stubEditor(def: WorkflowDefinition, overrides: Partial<WorkflowEditor> = {}): WorkflowEditor {
  return {
    definition: def,
    selection: { kind: 'node', id: 'n_2' },
    selectedIds: ['n_2'],
    validation: { valid: true, errors: [], warnings: [] },
    dispatch: vi.fn(),
    select: vi.fn(),
    pinData: vi.fn(),
    unpinData: vi.fn(),
    ...overrides,
  } as unknown as WorkflowEditor;
}

const run = {
  node_id: 'n_2',
  status: 'succeeded',
  inputs: { input: { items: [{ id: 1 }, { id: 2 }] } },
  outputs: { items: [{ shaped: true }] },
  duration_ms: 12,
};

describe('NodeDetailView', () => {
  it('renders the input, parameter, and output panes for the selected node', () => {
    render(
      <NodeDetailView open onClose={() => {}} editor={stubEditor(definition())} run={run} />,
    );
    const dialog = screen.getByRole('dialog', { name: /wait|shape/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Input' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Parameters' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Output' })).toBeInTheDocument();
    // 2 upstream items, 1 output item.
    expect(screen.getByText('2 items')).toBeInTheDocument();
    expect(screen.getByText('1 item')).toBeInTheDocument();
    expect(screen.getByText(/"shaped": true/)).toBeInTheDocument();
  });

  it('pins the current output into the workflow with one action', () => {
    const editor = stubEditor(definition());
    render(<NodeDetailView open onClose={() => {}} editor={editor} run={run} />);
    fireEvent.click(screen.getByRole('button', { name: /pin output/i }));
    expect(editor.pinData).toHaveBeenCalledWith(
      'n_2',
      expect.objectContaining({ outputs: { items: [{ shaped: true }] }, status: 'succeeded' }),
    );
  });

  it('shows pinned data, warns about a newer run, and unpins', () => {
    const def = definition();
    def.settings.pinned_data = {
      n_2: {
        pinned_at: '2026-11-22T09:00:00Z',
        outputs: { items: [{ pinned: true }] },
        status: 'succeeded',
      },
    };
    const editor = stubEditor(def);
    render(<NodeDetailView open onClose={() => {}} editor={editor} run={run} />);
    expect(screen.getByText(/"pinned": true/)).toBeInTheDocument();
    expect(screen.getByText(/a newer run exists/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /unpin/i }));
    expect(editor.unpinData).toHaveBeenCalledWith('n_2');
  });

  it('disables pinning in read-only mode and closes on Escape', () => {
    const onClose = vi.fn();
    render(
      <NodeDetailView
        open
        onClose={onClose}
        editor={stubEditor(definition())}
        run={run}
        readOnly
      />,
    );
    expect(screen.queryByRole('button', { name: /pin output/i })).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('explains why the output is empty before the node has run', () => {
    render(<NodeDetailView open onClose={() => {}} editor={stubEditor(definition())} run={null} />);
    expect(screen.getByText(/no output yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /pin output/i })).not.toBeInTheDocument();
  });
});
