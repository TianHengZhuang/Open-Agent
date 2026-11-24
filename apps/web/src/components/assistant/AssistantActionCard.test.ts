import { describe, expect, it } from 'vitest';
import { actionTarget } from './AssistantActionCard';

describe('actionTarget', () => {
  it('sends lifecycle actions to the right surface', () => {
    expect(actionTarget({ type: 'delete', status: 'ok', detail: '' })).toEqual({
      href: '/workflows',
      label: 'Open workflows',
    });
    expect(
      actionTarget({
        type: 'duplicate',
        status: 'ok',
        detail: '',
        workflow_id: 'wf-1',
      }),
    ).toEqual({ href: '/workflows/wf-1/edit', label: 'Open the copy' });
    expect(actionTarget({ type: 'list_credentials', status: 'ok', detail: '' })).toEqual({
      href: '/settings?tab=credentials',
      label: 'Open credentials',
    });
  });

  it('links saved-version actions back to the builder', () => {
    for (const type of ['versions', 'restore', 'variables', 'set_variable', 'schedule']) {
      expect(
        actionTarget({ type, status: 'ok', detail: '', workflow_id: 'wf-1' }),
      ).toEqual({ href: '/workflows/wf-1/edit', label: 'Open in builder' });
    }
    expect(actionTarget({ type: 'versions', status: 'ok', detail: '' })).toBeNull();
  });

  it('opens the exact execution when the chat knows it', () => {
    expect(
      actionTarget({
        type: 'run',
        status: 'ok',
        detail: '',
        data: { execution_id: 'exec-9' },
      }),
    ).toEqual({ href: '/runs/exec-9', label: 'View run' });
    expect(
      actionTarget({
        type: 'runs',
        status: 'ok',
        detail: '',
        data: { runs: [{ id: 'exec-1' }] },
      }),
    ).toEqual({ href: '/runs/exec-1', label: 'View run' });
    expect(
      actionTarget({
        type: 'runs',
        status: 'ok',
        detail: '',
        data: { runs: [{ id: 'exec-1' }, { id: 'exec-2' }] },
      }),
    ).toEqual({ href: '/runs', label: 'View runs' });
    expect(
      actionTarget({
        type: 'cancel',
        status: 'ok',
        detail: '',
        data: { execution_id: 'exec-3' },
      }),
    ).toEqual({ href: '/runs/exec-3', label: 'View execution' });
  });

  it('does not bounce agent actions into a redirect loop', () => {
    // /agents forwards back to the assistant, so the chat is already the surface.
    expect(actionTarget({ type: 'list_agents', status: 'ok', detail: '' })).toBeNull();
    expect(actionTarget({ type: 'create_agent', status: 'ok', detail: '' })).toBeNull();
  });
});
