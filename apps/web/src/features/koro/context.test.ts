import { buildFixPrompt, buildKoroContext } from './context';

describe('buildKoroContext', () => {
  it('falls back to the current route and omits unknown fields', () => {
    expect(buildKoroContext(null, '/assistant')).toEqual({ route: '/assistant' });
  });

  it('describes the open workflow without leaking definitions', () => {
    const context = buildKoroContext(
      {
        route: '/workflows/w1/edit',
        workflowId: 'w1',
        workflowName: 'Lead intake',
        nodeCount: 4,
        edgeCount: 3,
        steps: [
          { id: 'n1', name: 'Normalize', type: 'set' },
          { id: 'n2', name: 'Notify Slack', type: 'webhook' },
        ],
        validation: { errors: 1, warnings: 2 },
        lastRun: { status: 'failed', failed_nodes: ['Notify Slack'] },
      },
      '/ignored',
    );

    expect(context).toEqual({
      route: '/workflows/w1/edit',
      workflow: {
        id: 'w1',
        name: 'Lead intake',
        nodes: 4,
        edges: 3,
        steps: [
          { id: 'n1', name: 'Normalize', type: 'set' },
          { id: 'n2', name: 'Notify Slack', type: 'webhook' },
        ],
      },
      validation: { errors: 1, warnings: 2 },
      last_run: { status: 'failed', failed_nodes: ['Notify Slack'] },
    });
    expect(JSON.stringify(context)).not.toContain('definition');
  });

  it('caps the step outline sent with a turn', () => {
    const steps = Array.from({ length: 40 }, (_, index) => ({
      id: `n${index}`,
      name: `Step ${index}`,
      type: 'set',
    }));

    const context = buildKoroContext({ workflowId: 'w3', steps });

    expect(context.workflow?.steps).toHaveLength(30);
  });

  it('names a workflow that has no title', () => {
    expect(buildKoroContext({ workflowId: 'w2' })?.workflow?.name).toBe('Untitled workflow');
  });
});

describe('buildFixPrompt', () => {
  it('includes the node, error, code and payloads', () => {
    const prompt = buildFixPrompt(
      {
        node_id: 'n3',
        node_name: 'Notify Slack',
        node_type: 'webhook',
        error: 'connection refused',
        error_code: 'WEBHOOK_FAILED',
        inputs: { text: 'hello' },
        outputs: undefined,
      },
      { id: 'wf-1', name: 'Lead intake' },
    );

    expect(prompt).toContain('Lead intake');
    expect(prompt).toContain('Notify Slack');
    expect(prompt).toContain('connection refused');
    expect(prompt).toContain('WEBHOOK_FAILED');
    expect(prompt).toContain('"text":"hello"');
    expect(prompt).toContain('exact node setting');
  });

  it('truncates very large payloads', () => {
    const prompt = buildFixPrompt(
      {
        node_id: 'n1',
        node_name: 'Code',
        node_type: 'transform',
        error: 'boom',
        inputs: { blob: 'x'.repeat(5000) },
      },
      { id: 'wf-2', name: 'Big' },
    );

    expect(prompt.length).toBeLessThan(2500);
    expect(prompt).toContain('…');
  });

  it('handles a failure with no error text', () => {
    const prompt = buildFixPrompt(
      { node_id: 'n1', node_name: 'Step', node_type: 'set' },
      { id: 'wf-3', name: 'W' },
    );
    expect(prompt).toContain('unknown error');
  });
});
