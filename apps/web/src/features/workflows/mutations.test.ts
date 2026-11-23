import { describe, expect, it } from 'vitest';
import {
  applyMutations,
  createWorkflowDraft,
  describeDiff,
  hasOverlap,
  summarizeDiff,
} from './mutations';
import { emptyDefinition, type WorkflowDefinition } from './types';
import { createEditorState, editorReducer } from './use-workflow-editor';

function base(): WorkflowDefinition {
  return {
    ...emptyDefinition(),
    triggers: [{ id: 'trg', type: 'manual', name: 'Start', config: {} }],
    nodes: [
      {
        id: 'n_set',
        type: 'set',
        name: 'Normalize',
        position: { x: 80, y: 80 },
        config: { values: { ok: true } },
      },
    ],
    edges: [{ id: 'e1', from: 'trg', to: 'n_set' }],
  };
}

describe('applyMutations', () => {
  it('adds nodes, wires them and lays them out without overlap', () => {
    const outcome = applyMutations(base(), [
      { op: 'add_node', type: 'agent', label: 'Triage', id: 'n_agent' },
      { op: 'connect_nodes', sourceNodeId: 'n_set', targetNodeId: 'n_agent' },
    ]);

    expect(outcome.errors).toEqual([]);
    const added = outcome.definition.nodes.find((node) => node.id === 'n_agent');
    expect(added?.name).toBe('Triage');
    expect(added?.position).toBeDefined();
    expect(
      outcome.definition.edges.some((edge) => edge.from === 'n_set' && edge.to === 'n_agent'),
    ).toBe(true);

    const positions = outcome.definition.nodes.map((node) => `${node.position?.x}:${node.position?.y}`);
    expect(new Set(positions).size).toBe(positions.length);
    expect(outcome.diff.nodesAdded.map((node) => node.id)).toEqual(['n_agent']);
    expect(outcome.diff.edgesAdded).toEqual([{ from: 'n_set', to: 'n_agent' }]);
  });

  it('skips invalid operations and reports them in order', () => {
    const outcome = applyMutations(base(), [
      { op: 'connect_nodes', sourceNodeId: 'n_set', targetNodeId: 'missing' },
      { op: 'connect_nodes', sourceNodeId: 'trg', targetNodeId: 'n_set' },
      { op: 'update_node', nodeId: 'nope', label: 'x' },
      { op: 'remove_node', nodeId: 'gone' },
      { op: 'disconnect_nodes', edgeId: 'nope' },
    ]);

    expect(outcome.errors).toHaveLength(4);
    expect(outcome.definition.edges).toHaveLength(1);
  });

  it('updates config, disables, removes with edges and writes metadata', () => {
    const outcome = applyMutations(base(), [
      {
        op: 'update_node',
        nodeId: 'n_set',
        label: 'Shape input',
        config: { values: { ok: '{{ $json.ok }}' } },
        disabled: true,
      },
      { op: 'set_workflow_meta', name: 'Support flow', tags: ['support'], settings: { timezone: 'UTC' } },
    ]);

    const node = outcome.definition.nodes[0];
    expect(node.name).toBe('Shape input');
    expect(node.disabled).toBe(true);
    expect(node.config.values).toEqual({ ok: '{{ $json.ok }}' });
    expect(outcome.definition.settings.timezone).toBe('UTC');
    expect(outcome.meta).toEqual({ name: 'Support flow', tags: ['support'] });
    expect(outcome.diff.expressionsAdded).toBe(1);
    expect(outcome.diff.nodesUpdated[0].changed).toEqual(['label', 'config', 'disabled']);

    const removed = applyMutations(outcome.definition, [{ op: 'remove_node', nodeId: 'n_set' }]);
    expect(removed.definition.nodes).toEqual([]);
    expect(removed.definition.edges).toEqual([]);
    expect(removed.diff.nodesRemoved.map((item) => item.id)).toEqual(['n_set']);
  });

  it('flags unbound credentials and describes the diff', () => {
    const outcome = applyMutations(base(), [
      {
        op: 'add_node',
        type: 'webhook',
        label: 'Notify Slack',
        config: { url: 'https://hooks.slack.com/x', credential_id: '' },
      },
    ]);

    expect(outcome.diff.credentialsRequired).toContain('Notify Slack');
    expect(describeDiff(outcome.diff)).toContain('1 node added');
    expect(describeDiff(outcome.diff)).toContain('needs Notify Slack');
    expect(describeDiff(summarizeDiff(base(), base()))).toBe('No visible change');
  });

  it('auto-layouts a graph on request', () => {
    const original = base();
    original.nodes.push(
      { id: 'n_a', type: 'prompt', name: 'A', position: { x: 80, y: 80 }, config: {} },
      { id: 'n_b', type: 'agent', name: 'B', position: { x: 82, y: 82 }, config: {} },
      { id: 'n_c', type: 'set', name: 'C', position: { x: 84, y: 84 }, config: {} },
    );
    expect(hasOverlap(original)).toBe(true);

    const outcome = applyMutations(original, [{ op: 'auto_layout_graph' }]);
    expect(outcome.errors).toEqual([]);
    expect(hasOverlap(outcome.definition)).toBe(false);
  });

  it('imports a cURL command as an HTTP node', () => {
    const outcome = applyMutations(base(), [
      {
        op: 'import_curl',
        curl: "curl -X POST https://api.example.com/tickets -H 'Content-Type: application/json' -d '{\"id\": 1}'",
        nodeId: 'n_http',
      },
    ]);

    expect(outcome.errors).toEqual([]);
    const node = outcome.definition.nodes.find((item) => item.id === 'n_http');
    expect(node?.type).toBe('webhook');
    expect(node?.config).toMatchObject({ url: 'https://api.example.com/tickets', method: 'POST' });
    expect(hasOverlap(outcome.definition)).toBe(false);
  });

  it('reports unreadable cURL and missing fix targets', () => {
    const outcome = applyMutations(base(), [
      { op: 'import_curl', curl: 'definitely not curl' },
      { op: 'apply_node_fix', nodeId: 'n_missing', config: { retry: true } },
    ]);
    expect(outcome.errors).toEqual([
      'Could not read that cURL command',
      'No node “n_missing” to fix',
    ]);
  });

  it('applies an error fix to an existing node', () => {
    const outcome = applyMutations(base(), [
      { op: 'apply_node_fix', nodeId: 'n_set', config: { retry_on_fail: true } },
    ]);
    expect(outcome.errors).toEqual([]);
    expect(outcome.definition.nodes[0].config).toMatchObject({ retry_on_fail: true });
  });

  it('creates a workflow from scratch with a trigger and laid-out nodes', () => {
    const draft = createWorkflowDraft({
      name: 'Support triage',
      mutations: [{ op: 'add_node', type: 'agent', label: 'Triage' }],
    });
    expect(draft.definition.triggers).toHaveLength(1);
    expect(draft.definition.triggers[0].type).toBe('manual');
    expect(draft.definition.nodes[0].name).toBe('Triage');
    expect(draft.definition.nodes[0].position).toBeDefined();
  });

  it('is one atomic undo step in the editor', () => {
    const original = base();
    const outcome = applyMutations(original, [
      { op: 'add_node', id: 'n_prompt', type: 'prompt', label: 'Draft reply' },
      { op: 'connect_nodes', sourceNodeId: 'n_set', targetNodeId: 'n_prompt' },
    ]);

    let state = createEditorState(original);
    state = editorReducer(state, { type: 'replace', definition: outcome.definition });
    expect(state.definition.nodes).toHaveLength(2);

    state = editorReducer(state, { type: 'undo' });
    expect(state.definition.nodes).toHaveLength(1);
    expect(state.definition.nodes[0].name).toBe('Normalize');

    state = editorReducer(state, { type: 'redo' });
    expect(state.definition.nodes).toHaveLength(2);
  });
});
