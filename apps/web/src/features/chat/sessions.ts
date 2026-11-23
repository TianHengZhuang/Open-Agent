// Local chat sessions, persisted per workflow in localStorage. Nothing leaves
// the browser except the transcript the chat route receives for one turn.

import type { ChatMessage, ChatSession, ChatState } from './types';

export const CHAT_STATE_VERSION = 1 as const;
export const MAX_SESSIONS = 40;
export const MAX_MESSAGES_PER_SESSION = 200;

export function chatStorageKey(workflowId: string): string {
  return `oa:chat:${workflowId}:v1`;
}

function randomId(prefix: string): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `${prefix}_${crypto.randomUUID()}`;
  }
  return `${prefix}_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

export function newSessionId(): string {
  return randomId('chat');
}

export function newMessageId(): string {
  return randomId('msg');
}

export function emptyChatState(workflowId: string): ChatState {
  return { version: CHAT_STATE_VERSION, activeId: null, sessions: [], workflowId };
}

export function sessionTitleFrom(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (!clean) return 'New chat';
  return clean.length > 48 ? `${clean.slice(0, 47)}…` : clean;
}

function isMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== 'object') return false;
  const m = value as Partial<ChatMessage>;
  return (
    typeof m.id === 'string' &&
    (m.role === 'user' || m.role === 'assistant') &&
    typeof m.content === 'string' &&
    typeof m.createdAt === 'number'
  );
}

function isSessionMeta(value: unknown): value is Omit<ChatSession, 'messages'> & { messages: unknown[] } {
  if (!value || typeof value !== 'object') return false;
  const s = value as Partial<ChatSession>;
  return (
    typeof s.id === 'string' &&
    typeof s.title === 'string' &&
    typeof s.createdAt === 'number' &&
    typeof s.updatedAt === 'number' &&
    Array.isArray(s.messages)
  );
}

/** Tolerant parse: a corrupt store must never break the chat surface. */
export function parseChatState(raw: string | null, workflowId: string): ChatState {
  if (!raw) return emptyChatState(workflowId);
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return emptyChatState(workflowId);
  }
  if (!parsed || typeof parsed !== 'object') return emptyChatState(workflowId);
  const candidate = parsed as Partial<ChatState>;
  // Drop individual corrupt messages but keep the session: losing a whole
  // conversation because one row is malformed would be needlessly destructive.
  const sessions: ChatSession[] = (Array.isArray(candidate.sessions) ? candidate.sessions : [])
    .filter(isSessionMeta)
    .map((session) => ({
      id: session.id,
      title: session.title,
      createdAt: session.createdAt,
      updatedAt: session.updatedAt,
      messages: session.messages.filter(isMessage).slice(-MAX_MESSAGES_PER_SESSION),
    }))
    .slice(0, MAX_SESSIONS);
  const activeId =
    typeof candidate.activeId === 'string' && sessions.some((s) => s.id === candidate.activeId)
      ? candidate.activeId
      : sessions[0]?.id ?? null;
  return { version: CHAT_STATE_VERSION, activeId, sessions, workflowId };
}

export function loadChatState(workflowId: string): ChatState {
  if (typeof window === 'undefined') return emptyChatState(workflowId);
  try {
    return parseChatState(window.localStorage.getItem(chatStorageKey(workflowId)), workflowId);
  } catch {
    return emptyChatState(workflowId);
  }
}

export function saveChatState(state: ChatState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(chatStorageKey(state.workflowId), JSON.stringify(state));
  } catch {
    // Quota or private mode: the conversation stays in memory.
  }
}

export function clearChatStorage(workflowId: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(chatStorageKey(workflowId));
  } catch {
    // ignore
  }
}

export function activeSession(state: ChatState): ChatSession | null {
  if (!state.activeId) return null;
  return state.sessions.find((s) => s.id === state.activeId) ?? null;
}

export function createSession(
  state: ChatState,
  opts: { id?: string; now?: number; title?: string } = {},
): ChatState {
  const now = opts.now ?? Date.now();
  const session: ChatSession = {
    id: opts.id ?? newSessionId(),
    title: opts.title ?? 'New chat',
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
  return {
    ...state,
    activeId: session.id,
    sessions: [session, ...state.sessions].slice(0, MAX_SESSIONS),
  };
}

/** Return the active session id, creating a session when none is active. */
export function ensureSession(
  state: ChatState,
  opts: { id?: string; now?: number } = {},
): { state: ChatState; sessionId: string } {
  const current = activeSession(state);
  if (current) return { state, sessionId: current.id };
  const next = createSession(state, opts);
  return { state: next, sessionId: next.activeId! };
}

export function selectSession(state: ChatState, sessionId: string): ChatState {
  if (!state.sessions.some((s) => s.id === sessionId)) return state;
  return { ...state, activeId: sessionId };
}

export function renameSession(state: ChatState, sessionId: string, title: string): ChatState {
  const clean = title.replace(/\s+/g, ' ').trim().slice(0, 80);
  if (!clean) return state;
  return {
    ...state,
    sessions: state.sessions.map((s) =>
      s.id === sessionId ? { ...s, title: clean, updatedAt: Date.now() } : s,
    ),
  };
}

export function deleteSession(state: ChatState, sessionId: string): ChatState {
  const sessions = state.sessions.filter((s) => s.id !== sessionId);
  return {
    ...state,
    sessions,
    activeId: state.activeId === sessionId ? sessions[0]?.id ?? null : state.activeId,
  };
}

export function clearSessionMessages(state: ChatState, sessionId: string): ChatState {
  return {
    ...state,
    sessions: state.sessions.map((s) =>
      s.id === sessionId ? { ...s, messages: [], updatedAt: Date.now() } : s,
    ),
  };
}

export function appendMessage(
  state: ChatState,
  sessionId: string,
  message: ChatMessage,
): ChatState {
  return {
    ...state,
    sessions: state.sessions.map((s) =>
      s.id === sessionId
        ? {
            ...s,
            messages: [...s.messages, message].slice(-MAX_MESSAGES_PER_SESSION),
            updatedAt: message.createdAt,
          }
        : s,
    ),
  };
}

export function updateMessage(
  state: ChatState,
  sessionId: string,
  messageId: string,
  patch: Partial<ChatMessage>,
): ChatState {
  return {
    ...state,
    sessions: state.sessions.map((s) =>
      s.id === sessionId
        ? {
            ...s,
            messages: s.messages.map((m) => (m.id === messageId ? { ...m, ...patch } : m)),
            updatedAt: Date.now(),
          }
        : s,
    ),
  };
}

/** Transcript for the model: completed turns only, capped by the API too. */
export function transcriptFor(
  session: ChatSession | null,
  limit = 20,
): { role: 'user' | 'assistant'; content: string }[] {
  if (!session) return [];
  return session.messages
    .filter((m) => !m.pending && !m.error && m.content.trim())
    .slice(-limit)
    .map((m) => ({ role: m.role, content: m.content }));
}
