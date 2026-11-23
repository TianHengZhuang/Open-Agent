import * as React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { RunPanel } from './RunPanel';
import type { WorkflowRunResult } from '@/features/workflows/workflows-api';

function result(): WorkflowRunResult {
  return {
    execution_id: 'exec_1234567890',
    workflow_id: 'wf_1',
    status: 'failed',
    message: 'Run failed',
    node_runs: [
      {
        node_id: 'trg_1',
        node_type: 'manual',
        node_name: 'Start',
        status: 'succeeded',
        inputs: {},
        outputs: { items: [{ id: 1 }, { id: 2 }] },
        duration_ms: 4,
      },
      {
        node_id: 'n_1',
        node_type: 'http_request',
        node_name: 'Fetch',
        status: 'failed',
        inputs: { input: { items: [{ id: 1 }] } },
        outputs: {},
        error: 'connection refused',
        duration_ms: 120,
      },
    ],
    summary: { total: 2, succeeded: 1, failed: 1, skipped: 0, waiting: 0, duration_ms: 124 },
  };
}

describe('RunPanel debugging', () => {
  it('opens the failing step in the editor from Debug in editor', () => {
    const onDebugNode = vi.fn();
    render(
      <RunPanel
        result={result()}
        running={false}
        error={null}
        onClose={() => {}}
        onDebugNode={onDebugNode}
        onRerun={() => {}}
      />,
    );
    // Expand the failed row to reach its actions.
    fireEvent.click(screen.getByRole('button', { name: /fetch/i }));
    fireEvent.click(screen.getByRole('button', { name: /debug in editor/i }));
    expect(onDebugNode).toHaveBeenCalledWith('n_1');
  });

  it('labels the same action Inspect data for successful steps', () => {
    const onDebugNode = vi.fn();
    render(
      <RunPanel
        result={result()}
        running={false}
        error={null}
        onClose={() => {}}
        onDebugNode={onDebugNode}
        onRerun={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /start/i }));
    fireEvent.click(screen.getByRole('button', { name: /inspect data/i }));
    expect(onDebugNode).toHaveBeenCalledWith('trg_1');
  });

  it('shows item counts and error text inline in the row', () => {
    render(
      <RunPanel result={result()} running={false} error={null} onClose={() => {}} onRerun={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /fetch/i }));
    expect(screen.getByText(/connection refused/i)).toBeInTheDocument();
    expect(screen.getByText(/1 failed/)).toBeInTheDocument();
  });
});
