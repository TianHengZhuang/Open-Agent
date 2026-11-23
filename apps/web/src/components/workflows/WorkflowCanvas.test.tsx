import * as React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkflowCanvas } from './WorkflowCanvas';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import { emptyDefinition } from '@/features/workflows/types';
import type { WorkflowDefinition } from '@/features/workflows/types';

function definition(): WorkflowDefinition {
  return {
    ...emptyDefinition(),
    triggers: [{ id: 'trg_1', type: 'manual', name: 'Start', config: {} }],
    nodes: [
      { id: 'n_1', type: 'tool', name: 'Fetch', position: { x: 400, y: 80 }, config: {} },
      { id: 'n_2', type: 'set', name: 'Shape', position: { x: 800, y: 80 }, config: {} },
    ],
    edges: [
      { id: 'e_1', from: 'trg_1', to: 'n_1', condition: { when: 'always' } },
      { id: 'e_2', from: 'n_1', to: 'n_2', condition: { when: 'always' } },
    ],
  };
}

function stubEditor(def: WorkflowDefinition): WorkflowEditor {
  return {
    definition: def,
    selection: null,
    selectedIds: [],
    validation: { valid: true, errors: [], warnings: [] },
    select: vi.fn(),
    dispatch: vi.fn(),
    pendingEdge: null,
    setPendingEdge: vi.fn(),
    hasClipboard: false,
  } as unknown as WorkflowEditor;
}

describe('WorkflowCanvas run overlay', () => {
  it('shows item counts, failed labels and error tooltips per node', () => {
    const { container } = render(
      <WorkflowCanvas
        editor={stubEditor(definition())}
        runStatus={{ trg_1: 'succeeded', n_1: 'failed' }}
        runItems={{ trg_1: 1, n_1: 0 }}
        runErrors={{ n_1: 'connection refused' }}
      />,
    );
    expect(screen.getByText('1 item')).toBeInTheDocument();
    expect(screen.getByText('0 items')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    const titles = Array.from(container.querySelectorAll('title')).map((t) => t.textContent);
    expect(titles).toContain('connection refused');
  });

  it('summarises the last run and reopens the results panel from the chip', () => {
    const onOpenRunPanel = vi.fn();
    render(
      <WorkflowCanvas
        editor={stubEditor(definition())}
        lastRun={{ status: 'failed', succeeded: 1, failed: 1, skipped: 0, waiting: 0 }}
        onOpenRunPanel={onOpenRunPanel}
      />,
    );
    const chip = screen.getByRole('button', { name: /last run failed: 1 succeeded, 1 failed/i });
    expect(chip).toHaveTextContent('1 ok');
    fireEvent.click(chip);
    expect(onOpenRunPanel).toHaveBeenCalled();
  });

  it('shows an in-flight chip and pulses the step under test', () => {
    const { container } = render(
      <WorkflowCanvas
        editor={stubEditor(definition())}
        running
        activeNodeIds={['n_1']}
      />,
    );
    expect(screen.getByText('Running…')).toBeInTheDocument();
    expect(container.querySelectorAll('[aria-label="Running this step"]')).toHaveLength(1);
  });

  it('opens the detail view on double-click', () => {
    const onOpenNodeDetail = vi.fn();
    const { container } = render(
      <WorkflowCanvas editor={stubEditor(definition())} onOpenNodeDetail={onOpenNodeDetail} />,
    );
    const node = container.querySelector('[data-item-id="n_1"]')!;
    fireEvent.doubleClick(node);
    expect(onOpenNodeDetail).toHaveBeenCalledWith('n_1');
  });
});
