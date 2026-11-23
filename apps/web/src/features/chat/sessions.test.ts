import {
  activeSession,
  appendMessage,
  chatStorageKey,
  clearSessionMessages,
  createSession,
  deleteSession,
  emptyChatState,
  ensureSession,
  loadChatState,
  parseChatState,
  renameSession,
  saveChatState,
  selectSession,
  sessionTitleFrom,
  transcriptFor,
  updateMessage,
  newMessageId,
} from './sessions';
import type { ChatMessage } from './types';

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: newMessageId(),
    role: 'user',
    content: 'hello',
    createdAt: Date.now(),
    ...overrides,
  };
}

describe('chat sessions', () => {
  it('creates and activates a session without mutating the old state', () => {
    const state = emptyChatState('wf_1');
    const next = createSession(state, { id: 'chat_a', now: 1000 });
    expect(next.sessions).toHaveLength(1);
    expect(next.activeId).toBe('chat_a');
    expect(state.sessions).toHaveLength(0);
    expect(activeSession(next)?.title).toBe('New chat');
  });

  it('ensureSession reuses the active session and only creates when empty', () => {
    const first = ensureSession(emptyChatState('wf_1'), { id: 'chat_a' });
    expect(first.sessionId).toBe('chat_a');
    const second = ensureSession(first.state, { id: 'chat_b' });
    expect(second.sessionId).toBe('chat_a');
    expect(second.state).toBe(first.state);
  });

  it('appends, updates and caps messages per session', () => {
    let state = createSession(emptyChatState('wf_1'), { id: 'chat_a', now: 1 });
    const first = message({ id: 'm1', content: 'hi', createdAt: 10 });
    state = appendMessage(state, 'chat_a', first);
    state = appendMessage(state, 'chat_a', message({ id: 'm2', role: 'assistant', content: 'hey', createdAt: 11 }));
    expect(activeSession(state)?.messages.map((m) => m.id)).toEqual(['m1', 'm2']);
    expect(activeSession(state)?.updatedAt).toBe(11);

    state = updateMessage(state, 'chat_a', 'm2', { content: 'hey!', pending: false });
    expect(activeSession(state)?.messages[1].content).toBe('hey!');
    // Unknown ids are ignored rather than throwing.
    expect(updateMessage(state, 'chat_a', 'missing', { content: 'x' })).toBeTruthy();
  });

  it('titles a session from the first turn', () => {
    expect(sessionTitleFrom('  Book   a   flight  ')).toBe('Book a flight');
    expect(sessionTitleFrom('x'.repeat(80))).toHaveLength(48);
  });

  it('renames, selects, clears and deletes sessions', () => {
    let state = createSession(createSession(emptyChatState('wf_1'), { id: 'a' }), { id: 'b' });
    state = selectSession(state, 'a');
    expect(state.activeId).toBe('a');
    state = renameSession(state, 'a', '  Support  ');
    expect(state.sessions.find((s) => s.id === 'a')?.title).toBe('Support');
    state = appendMessage(state, 'a', message());
    state = clearSessionMessages(state, 'a');
    expect(state.sessions.find((s) => s.id === 'a')?.messages).toEqual([]);
    state = deleteSession(state, 'a');
    expect(state.sessions.map((s) => s.id)).toEqual(['b']);
    expect(state.activeId).toBe('b');
    state = deleteSession(state, 'b');
    expect(state.activeId).toBeNull();
  });

  it('parses a stored state defensively', () => {
    const fresh = emptyChatState('wf_1');
    expect(parseChatState(null, 'wf_1')).toEqual(fresh);
    expect(parseChatState('not json', 'wf_1')).toEqual(fresh);
    expect(parseChatState('{"sessions":"nope"}', 'wf_1')).toEqual(fresh);

    const stored = JSON.stringify({
      version: 1,
      activeId: 'gone',
      sessions: [
        {
          id: 'chat_a',
          title: 'Kept',
          createdAt: 1,
          updatedAt: 2,
          messages: [
            { id: 'm1', role: 'user', content: 'hi', createdAt: 3 },
            { id: 'm2', role: 'robot', content: 'bad role', createdAt: 4 },
          ],
        },
        { id: 'broken' },
      ],
    });
    const parsed = parseChatState(stored, 'wf_1');
    expect(parsed.sessions).toHaveLength(1);
    expect(parsed.sessions[0].title).toBe('Kept');
    // An unknown active id falls back to the first session.
    expect(parsed.activeId).toBe('chat_a');
  });

  it('round-trips through localStorage per workflow', () => {
    // The shared test setup stubs localStorage with no-ops; use a real map here.
    const store = new Map<string, string>();
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => void store.set(key, String(value)),
        removeItem: (key: string) => void store.delete(key),
        clear: () => store.clear(),
        key: (index: number) => [...store.keys()][index] ?? null,
        get length() {
          return store.size;
        },
      },
    });

    const state = appendMessage(
      createSession(emptyChatState('wf_9'), { id: 'chat_z' }),
      'chat_z',
      message({ content: 'persisted' }),
    );
    saveChatState(state);
    expect(loadChatState('wf_9').sessions[0].messages[0].content).toBe('persisted');
    expect(loadChatState('wf_other').sessions).toEqual([]);
    expect(chatStorageKey('wf_9')).toBe('oa:chat:wf_9:v1');
  });

  it('sends only completed turns to the model', () => {
    let state = createSession(emptyChatState('wf_1'), { id: 'chat_a' });
    state = appendMessage(state, 'chat_a', message({ id: 'm1', content: 'first', createdAt: 1 }));
    state = appendMessage(state, 'chat_a', message({ id: 'm2', role: 'assistant', content: '', pending: true, createdAt: 2 }));
    state = appendMessage(state, 'chat_a', message({ id: 'm3', role: 'assistant', content: 'failed', error: 'boom', createdAt: 3 }));
    state = appendMessage(state, 'chat_a', message({ id: 'm4', role: 'assistant', content: 'reply', createdAt: 4 }));

    expect(transcriptFor(activeSession(state))).toEqual([
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'reply' },
    ]);
    expect(transcriptFor(null)).toEqual([]);
  });
});
