import * as React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { AssistantComposer } from './AssistantComposer';
import type { MentionResource } from '@/features/assistant/mentions';

vi.mock('@/features/koro/koro-api', () => ({
  useKoroStatus: () => ({
    data: {
      ready: true,
      provider: 'Ollama',
      model: 'llama3.2:1b',
      local: true,
      base_url: 'http://127.0.0.1:11434/v1',
    },
    isLoading: false,
  }),
  useKoroSettings: () => ({ data: undefined }),
  useLocalModels: () => ({ data: undefined, refetch: vi.fn(), isLoading: false }),
  useActivateModel: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateKoroSettings: () => ({ mutateAsync: vi.fn(), isPending: false, reset: vi.fn() }),
  useProviderModels: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const resources: MentionResource[] = [
  { kind: 'workflow', id: 'wf_1', label: 'Support triage', detail: 'ACTIVE' },
  { kind: 'credential', id: 'c1', label: 'Slack production', detail: 'slack' },
];

function setup(overrides: Partial<React.ComponentProps<typeof AssistantComposer>> = {}) {
  const props = {
    value: '',
    onChange: vi.fn(),
    onSend: vi.fn(),
    resources,
    starters: ['Build a webhook to Slack'],
    ...overrides,
  };
  const view = render(<AssistantComposer {...props} />);
  return { ...props, view };
}

describe('AssistantComposer', () => {
  it('sends on Enter and keeps Shift+Enter for newlines', () => {
    const onSend = vi.fn();
    setup({ value: 'Build a digest', onSend });
    const box = screen.getByLabelText('Message the assistant');

    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith({ text: 'Build a digest', attachments: [] });

    onSend.mockClear();
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('turns the send button into Stop while a reply is streaming', () => {
    const onStop = vi.fn();
    setup({ value: '', busy: true, onStop });

    const stop = screen.getByLabelText('Stop the assistant');
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText('Send message')).toBeNull();
  });

  it('shows an active per-chat model override and can clear it', () => {
    const onModelOverride = vi.fn();
    setup({
      modelOverride: { model: 'llama3.2:3b', base_url: 'http://127.0.0.1:11434/v1' },
      onModelOverride,
    });

    expect(screen.getByText(/llama3\.2:3b/)).not.toBeNull();
    expect(screen.getByText('chat')).not.toBeNull();

    fireEvent.click(screen.getByLabelText('Use the default model for this chat'));
    expect(onModelOverride).toHaveBeenCalledWith(null);
  });

  it('filters the @ picker and inserts the chosen resource', () => {
    const { onChange } = setup({ value: 'Use @slack', onChange: vi.fn() });
    const box = screen.getByLabelText('Message the assistant');

    // Caret at the end of the token opens the picker.
    fireEvent.change(box, { target: { value: 'Use @slack' } });
    (box as HTMLTextAreaElement).setSelectionRange(10, 10);
    fireEvent.keyUp(box);
    const suggestion = screen.getByRole('option', { name: /Slack production/ });
    fireEvent.mouseDown(suggestion);

    expect(onChange).toHaveBeenCalledWith('Use @Slack production (credential c1) ');
  });
});
