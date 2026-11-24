import { describe, expect, it } from 'vitest';
import { exportN8nJson, fromN8nWorkflow, isN8nWorkflow, toN8nWorkflow } from './n8n-format';
import { emptyDefinition } from './types';

const workflow = {
  name: 'Support triage',
  tags: [{ name: 'support' }],
  nodes: [
    {
      name: 'Webhook',
      type: 'n8n-nodes-base.webhook',
      typeVersion: 1,
      position: [100, 200] as [number, number],
      parameters: { path: 'tickets', httpMethod: 'POST' },
    },
    {
      name: 'Set fields',
      type: 'n8n-nodes-base.set',
      typeVersion: 1,
      position: [340, 200] as [number, number],
      parameters: { values: { priority: 'high' } },
    },
    {
      name: 'Call API',
      type: 'n8n-nodes-base.httpRequest',
      position: [580, 200] as [number, number],
      parameters: { url: 'https://api.example.com/tickets', method: 'POST' },
    },
    {
      name: 'Sticky',
      type: 'n8n-nodes-base.stickyNote',
      position: [0, 0] as [number, number],
      parameters: {},
    },
    {
      name: 'Model',
      type: '@n8n/n8n-nodes-langchain.lmChatOpenAi',
      position: [820, 200] as [number, number],
      parameters: {},
    },
    {
      name: 'Mystery',
      type: 'n8n-nodes-community.mystery',
      position: [900, 260] as [number, number],
      parameters: { foo: 'bar' },
    },
  ],
  connections: {
    Webhook: { main: [[{ node: 'Set fields', type: 'main', index: 0 }]] },
    'Set fields': { main: [[{ node: 'Call API', type: 'main', index: 0 }]] },
  },
  settings: { executionOrder: 'v1' },
  pinData: { 'Call API': [{ json: { ok: true } }] },
};

describe('n8n import', () => {
  it('detects n8n workflows and maps triggers, nodes and connections', () => {
    expect(isN8nWorkflow(workflow)).toBe(true);
    expect(isN8nWorkflow({ schema_version: '1.0' })).toBe(false);

    const result = fromN8nWorkflow(workflow);
    expect(result.ok).toBe(true);
    expect(result.name).toBe('Support triage');
    expect(result.tags).toEqual(['support']);
    expect(result.definition?.triggers).toHaveLength(1);
    expect(result.definition?.triggers[0].type).toBe('webhook');
    expect(result.definition?.triggers[0].config).toMatchObject({ path: 'tickets', method: 'POST' });
    expect(result.definition?.nodes.map((node) => node.name)).toEqual([
      'Set fields',
      'Call API',
      'Mystery',
    ]);
    expect(result.definition?.nodes[1].config.url).toBe('https://api.example.com/tickets');
    expect(result.definition?.edges).toHaveLength(2);
    expect(result.definition?.settings).toMatchObject({ executionOrder: 'v1' });
    expect(result.definition?.settings.pinned_data).toBeDefined();
    expect(result.warnings).toEqual(
      expect.arrayContaining([
        'Skipped sub-node “Model” (@n8n/n8n-nodes-langchain.lmChatOpenAi).',
        '“Mystery” (n8n-nodes-community.mystery) mapped to a transform node.',
      ]),
    );
  });

  it('keeps unmapped parameters on transform nodes', () => {
    const result = fromN8nWorkflow(workflow);
    const mystery = result.definition?.nodes.find((node) => node.name === 'Mystery');
    expect(mystery?.type).toBe('transform');
    expect(mystery?.config.n8n_parameters).toEqual({ foo: 'bar' });
  });

  it('imports node error/retry settings and the error output', () => {
    const result = fromN8nWorkflow({
      nodes: [
        {
          name: 'Call API',
          type: 'n8n-nodes-base.httpRequest',
          position: [0, 0],
          parameters: { url: 'https://api.example.com' },
          onError: 'continueErrorOutput',
          retryOnFail: true,
          maxTries: 4,
          waitBetweenTries: 500,
          alwaysOutputData: true,
          executeOnce: true,
        },
        { name: 'Recover', type: 'n8n-nodes-base.set', position: [200, 0], parameters: {} },
      ],
      connections: {
        'Call API': { main: [[], [{ node: 'Recover', type: 'main', index: 1 }]] },
      },
    });

    const node = result.definition?.nodes.find((entry) => entry.name === 'Call API');
    expect(node).toMatchObject({
      on_error: 'continueErrorOutput',
      retry_on_fail: true,
      max_tries: 4,
      wait_between_tries_ms: 500,
      always_output_data: true,
      execute_once: true,
    });
    const recover = result.definition?.nodes.find((entry) => entry.name === 'Recover');
    const errorEdge = result.definition?.edges.find((edge) => edge.to === recover?.id);
    expect(errorEdge?.connection_type).toBe('error');
    expect(errorEdge?.from_port).toBe('error');
  });

  it('maps schedule triggers to cron config', () => {
    const result = fromN8nWorkflow({
      nodes: [
        {
          name: 'Every morning',
          type: 'n8n-nodes-base.scheduleTrigger',
          position: [0, 0],
          parameters: { rule: { interval: [{ cronExpression: '0 8 * * MON-FRI' }] } },
        },
      ],
      connections: {},
    });
    expect(result.definition?.triggers[0]).toMatchObject({
      type: 'schedule',
      config: { cron: '0 8 * * MON-FRI' },
    });
  });
});

describe('n8n export', () => {
  it('exports triggers, nodes, edges and pins', () => {
    const definition = {
      ...emptyDefinition(),
      triggers: [
        {
          id: 'trg',
          type: 'schedule' as const,
          name: 'Every morning',
          position: { x: 10, y: 20 },
          config: { cron: '0 8 * * *' },
        },
      ],
      nodes: [
        {
          id: 'n1',
          type: 'set' as const,
          name: 'Shape',
          position: { x: 200, y: 20 },
          config: { values: { ok: true } },
        },
      ],
      edges: [{ id: 'e1', from: 'trg', to: 'n1' }],
      settings: {
        pinned_data: {
          n1: { pinned_at: '2026-01-01T00:00:00Z', outputs: { items: [{ ok: true }] } },
        },
      },
    };

    const exported = toN8nWorkflow(definition, { name: 'Morning', tags: ['digest'] });
    expect(exported.name).toBe('Morning');
    expect(exported.nodes.map((node) => node.type)).toEqual([
      'n8n-nodes-base.scheduleTrigger',
      'n8n-nodes-base.set',
    ]);
    expect(exported.connections).toEqual({
      'Every morning': { main: [[{ node: 'Shape', type: 'main', index: 0 }]] },
    });
    expect(exported.pinData?.['Shape']).toEqual([{ json: { ok: true } }]);
    expect(JSON.parse(exportN8nJson(definition)).nodes).toHaveLength(2);
  });

  it('round-trips a graph through n8n JSON', () => {
    const original = {
      ...emptyDefinition(),
      triggers: [
        { id: 'trg', type: 'manual' as const, name: 'Start', position: { x: 0, y: 0 }, config: {} },
      ],
      nodes: [
        {
          id: 'n1',
          type: 'condition' as const,
          name: 'IF urgent',
          position: { x: 240, y: 0 },
          config: { expression: '{{ $json.urgent }}' },
        },
        {
          id: 'n2',
          type: 'webhook' as const,
          name: 'Notify',
          position: { x: 480, y: 0 },
          config: { url: 'https://example.com', method: 'POST' },
        },
      ],
      edges: [
        { id: 'e1', from: 'trg', to: 'n1' },
        { id: 'e2', from: 'n1', to: 'n2', label: '1' },
      ],
    };

    const back = fromN8nWorkflow(toN8nWorkflow(original));
    expect(back.definition?.triggers[0].name).toBe('Start');
    expect(back.definition?.nodes.map((node) => node.name)).toEqual(['IF urgent', 'Notify']);
    expect(back.definition?.edges).toHaveLength(2);
    expect(back.definition?.nodes[1].config).toMatchObject({
      url: 'https://example.com',
      method: 'POST',
    });
  });
});
