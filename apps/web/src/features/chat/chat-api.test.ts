import { chatEndpoint, parseSseBuffer, streamWorkflowChat } from './chat-api';
import type { ChatStreamEvent } from './types';

const encoder = new TextEncoder();

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function fakeResponse(chunks: string[]): Response {
  let index = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          index < chunks.length
            ? { value: encoder.encode(chunks[index++]), done: false }
            : { value: undefined, done: true },
        cancel: async () => {},
      }),
    },
  } as unknown as Response;
}

const donePayload = {
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
      duration_ms: 12,
      error: null,
      reason: null,
    },
  ],
  summary: { total: 2, succeeded: 2, failed: 0, skipped: 0, waiting: 0, duration_ms: 12 },
  error: null,
  model: 'qwen2.5:0.5b-instruct',
  model_skipped: false,
};

describe('SSE parsing', () => {
  it('keeps an incomplete frame as the rest of the buffer', () => {
    const first = parseSseBuffer('event: token\ndata: {"text":"He"}\n\nevent: tok');
    expect(first.events).toEqual([{ type: 'token', data: { text: 'He' } }]);
    expect(first.rest).toBe('event: tok');

    const second = parseSseBuffer(`${first.rest}en\ndata: {"text":"y"}\n\n`);
    expect(second.events).toEqual([{ type: 'token', data: { text: 'y' } }]);
    expect(second.rest).toBe('');
  });

  it('handles CRLF frames and ignores unknown or malformed events', () => {
    const buffer =
      'event: token\r\ndata: {"text":"a"}\r\n\r\n' +
      'event: nope\ndata: {"x":1}\n\n' +
      'event: token\ndata: not-json\n\n' +
      'event: done\ndata: {"reply":"hi"}\n\n';
    const { events } = parseSseBuffer(buffer);
    expect(events.map((e) => e.type)).toEqual(['token', 'done']);
  });
});

describe('streamWorkflowChat', () => {
  it('posts the turn, streams tokens and resolves the done payload', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const events: ChatStreamEvent[] = [];
    const fetcher = async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return fakeResponse([
        sse('meta', { session_id: 's1', model: 'm', provider: 'Ollama', local: true }),
        sse('token', { text: 'Hel' }),
        // Split a frame mid-way to prove buffering works across chunks.
        sse('token', { text: 'lo ' }).slice(0, 12),
        sse('token', { text: 'lo ' }).slice(12) + sse('done', donePayload),
      ]);
    };

    const done = await streamWorkflowChat(
      {
        orgId: 'org_1',
        workflowId: 'wf_1',
        message: 'Hi',
        sessionId: 's1',
        history: [{ role: 'user', content: 'earlier' }],
        files: [
          { name: 'a.txt', mimeType: 'text/plain', dataUrl: 'data:text/plain;base64,aGk=', size: 22 },
        ],
      },
      { fetcher, onEvent: (event) => events.push(event) },
    );

    expect(done.reply).toBe('Hello there');
    expect(events.map((e) => e.type)).toEqual(['meta', 'token', 'token', 'done']);

    expect(calls[0].url).toBe(chatEndpoint('org_1', 'wf_1'));
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body).toMatchObject({
      message: 'Hi',
      session_id: 's1',
      history: [{ role: 'user', content: 'earlier' }],
      files: [{ name: 'a.txt', mime_type: 'text/plain', data_url: 'data:text/plain;base64,aGk=' }],
    });
    expect(body.definition).toBeNull();
    expect((calls[0].init?.headers as Record<string, string>)['X-Organization-ID']).toBe('org_1');
  });

  it('forwards the studio canvas definition', async () => {
    let sent: Record<string, unknown> = {};
    const fetcher = async (_url: string, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body));
      return fakeResponse([sse('done', donePayload)]);
    };
    await streamWorkflowChat(
      {
        orgId: 'org_1',
        workflowId: 'wf_1',
        message: 'Hi',
        sessionId: 's1',
        history: [],
        definition: { triggers: [], nodes: [], edges: [] },
      },
      { fetcher },
    );
    expect(sent.definition).toEqual({ triggers: [], nodes: [], edges: [] });
  });

  it('surfaces workflow errors from the stream', async () => {
    const fetcher = async () =>
      fakeResponse([sse('error', { message: 'Model endpoint refused the connection' })]);
    await expect(
      streamWorkflowChat(
        { orgId: 'org_1', workflowId: 'wf_1', message: 'Hi', sessionId: 's1', history: [] },
        { fetcher },
      ),
    ).rejects.toThrow('Model endpoint refused the connection');
  });

  it('reports HTTP errors with the API message', async () => {
    const fetcher = async () =>
      ({
        ok: false,
        status: 409,
        text: async () => JSON.stringify({ detail: { error: 'Workflow has no versions to run' } }),
      }) as unknown as Response;
    await expect(
      streamWorkflowChat(
        { orgId: 'org_1', workflowId: 'wf_1', message: 'Hi', sessionId: 's1', history: [] },
        { fetcher },
      ),
    ).rejects.toThrow('Workflow has no versions to run');
  });

  it('fails when the stream ends without a done event', async () => {
    const fetcher = async () => fakeResponse([sse('token', { text: 'partial' })]);
    await expect(
      streamWorkflowChat(
        { orgId: 'org_1', workflowId: 'wf_1', message: 'Hi', sessionId: 's1', history: [] },
        { fetcher },
      ),
    ).rejects.toThrow(/ended before/);
  });
});
