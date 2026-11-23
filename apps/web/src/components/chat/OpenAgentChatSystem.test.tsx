import * as React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { OpenAgentChatSystem } from './OpenAgentChatSystem';
import { streamWorkflowChat } from '@/features/chat/chat-api';
import type { ChatStreamDone } from '@/features/chat/types';

vi.mock('@/context/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrgId: 'org_1' }),
}));
vi.mock('@/features/chat/chat-api', () => ({
  streamWorkflowChat: vi.fn(),
}));
// The shared setup stubs localStorage with no-ops; the chat only needs it to exist.
vi.mock('@/features/chat/sessions', async () => {
  const actual = await vi.importActual<typeof import('@/features/chat/sessions')>(
    '@/features/chat/sessions',
  );
  return {
    ...actual,
    loadChatState: (workflowId: string) => actual.emptyChatState(workflowId),
    saveChatState: () => {},
  };
});

const mockedStream = vi.mocked(streamWorkflowChat);

function done(overrides: Partial<ChatStreamDone> = {}): ChatStreamDone {
  return {
    session_id: 's1',
    status: 'completed',
    reply: 'Hello there',
    reply_node_id: 'reply',
    execution_id: 'exec_1',
    workflow_id: 'wf_1',
    steps: [
      {
        node_id: 'reply',
        node_type: 'prompt',
        node_name: 'Reply',
        status: 'succeeded',
        duration_ms: 40,
        error: null,
        reason: null,
      },
    ],
    summary: { total: 2, succeeded: 2, failed: 0, skipped: 0, waiting: 0, duration_ms: 42 },
    error: null,
    model: 'llama3.2:1b',
    model_skipped: false,
    ...overrides,
  };
}

function typeAndSend(text: string) {
  const input = screen.getByLabelText('Chat message');
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: 'Enter' });
  return input;
}

describe('OpenAgentChatSystem', () => {
  beforeEach(() => {
    mockedStream.mockReset();
  });

  it('streams a reply and exposes the step trace', async () => {
    mockedStream.mockImplementation(async (_request, options) => {
      options?.onEvent?.({
        type: 'meta',
        data: { session_id: 's1', model: 'llama3.2:1b', provider: 'Ollama', local: true },
      });
      options?.onEvent?.({ type: 'token', data: { text: 'Hello ' } });
      options?.onEvent?.({ type: 'token', data: { text: 'there' } });
      return done();
    });

    const onInspectNode = vi.fn();
    render(
      <OpenAgentChatSystem
        workflowId="wf_1"
        workflowName="Support bot"
        onInspectNode={onInspectNode}
      />,
    );

    typeAndSend('Hi there');

    await waitFor(() => expect(screen.getByText('Hello there')).toBeInTheDocument());
    expect(mockedStream).toHaveBeenCalledTimes(1);
    const [request] = mockedStream.mock.calls[0];
    expect(request).toMatchObject({
      orgId: 'org_1',
      workflowId: 'wf_1',
      message: 'Hi there',
      history: [],
    });
    expect(screen.getByText(/Local model · llama3.2:1b/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /1 step/ }));
    expect(screen.getByText('Reply')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /inspect in canvas/i }));
    expect(onInspectNode).toHaveBeenCalledWith('reply', 'exec_1');
  });

  it('keeps the previous turns as chat history', async () => {
    let call = 0;
    mockedStream.mockImplementation(async (request) => {
      call += 1;
      return done({ reply: call === 1 ? 'First reply' : 'Second reply' });
    });

    render(<OpenAgentChatSystem workflowId="wf_1" />);
    typeAndSend('One');
    await waitFor(() => expect(screen.getByText('First reply')).toBeInTheDocument());
    typeAndSend('Two');
    await waitFor(() => expect(screen.getByText('Second reply')).toBeInTheDocument());

    const second = mockedStream.mock.calls[1][0];
    expect(second.history).toEqual([
      { role: 'user', content: 'One' },
      { role: 'assistant', content: 'First reply' },
    ]);
  });

  it('explains a missing model step instead of faking a reply', async () => {
    mockedStream.mockImplementation(async () => done({ reply: '', model_skipped: true }));
    render(<OpenAgentChatSystem workflowId="wf_1" />);
    typeAndSend('Hi');
    await waitFor(() => expect(screen.getByText(/No model step ran/)).toBeInTheDocument());
  });

  it('surfaces a failed run', async () => {
    mockedStream.mockImplementation(async () =>
      done({ status: 'failed', reply: '', error: 'Reply: connection refused' }),
    );
    render(<OpenAgentChatSystem workflowId="wf_1" />);
    typeAndSend('Hi');
    await waitFor(() => expect(screen.getByText('Reply: connection refused')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /retry this turn/i })).toBeInTheDocument();
  });

  it('starts from a starter prompt and titles the session', async () => {
    mockedStream.mockImplementation(async () => done({ reply: 'Sure' }));
    render(
      <OpenAgentChatSystem
        workflowId="wf_1"
        workflowName="Support bot"
        starters={['Where is my order?']}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Where is my order?' }));
    await waitFor(() => expect(screen.getByText('Sure')).toBeInTheDocument());
    expect(mockedStream.mock.calls[0][0].message).toBe('Where is my order?');

    fireEvent.click(screen.getByRole('button', { name: 'Show chat list' }));
    const rail = within(screen.getByRole('complementary', { name: 'Chat sessions' }));
    expect(rail.getByTitle('Where is my order?')).toBeInTheDocument();
    expect(rail.getByRole('button', { name: 'Delete Where is my order?' })).toBeInTheDocument();
  });

  it('disables sending for a read-only workflow', () => {
    render(<OpenAgentChatSystem workflowId="wf_1" disabled />);
    expect(screen.getByLabelText('Chat message')).toBeDisabled();
    expect(screen.getByLabelText('Send message')).toBeDisabled();
  });

  it('passes the unsaved studio definition with each turn', async () => {
    mockedStream.mockImplementation(async () => done({ reply: 'ok' }));
    render(
      <OpenAgentChatSystem
        workflowId="wf_1"
        definition={{ triggers: [], nodes: [], edges: [] } as never}
      />,
    );
    typeAndSend('Hi');
    await waitFor(() => expect(screen.getByText('ok')).toBeInTheDocument());
    expect(mockedStream.mock.calls[0][0].definition).toEqual({
      triggers: [],
      nodes: [],
      edges: [],
    });
  });
});
