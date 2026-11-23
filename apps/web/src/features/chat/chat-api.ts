// Streaming chat client for POST /workflows/{id}/chat (SSE over fetch).
// EventSource cannot POST, so the stream is read straight off the response.

import { API_BASE } from '@/lib/api';
import type { ChatAttachment, ChatStreamDone, ChatStreamEvent } from './types';

export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatRequest {
  orgId: string | null;
  workflowId: string;
  message: string;
  sessionId: string;
  history: ChatTurn[];
  files?: ChatAttachment[];
  /** The studio sends its unsaved canvas so chat tests the same graph. */
  definition?: unknown;
}

export type ChatFetcher = (input: string, init?: RequestInit) => Promise<Response>;

export function chatEndpoint(orgId: string | null, workflowId: string): string {
  return `${API_BASE}/organizations/${orgId ?? ''}/workflows/${workflowId}/chat`;
}

function csrfToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

interface SseFrame {
  event: string;
  data: unknown;
}

function parseFrame(frame: string): SseFrame | null {
  let event = 'message';
  const data: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
  }
  if (data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join('\n')) };
  } catch {
    return null;
  }
}

/**
 * Split a growing SSE buffer into complete events.
 * Returns the unconsumed tail so the caller can keep appending.
 */
export function parseSseBuffer(buffer: string): { events: ChatStreamEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n');
  const frames = normalized.split('\n\n');
  const rest = frames.pop() ?? '';
  const events: ChatStreamEvent[] = [];
  for (const frame of frames) {
    if (!frame.trim()) continue;
    const parsed = parseFrame(frame);
    if (!parsed) continue;
    const { event, data } = parsed;
    if (event === 'meta') events.push({ type: 'meta', data: data as never });
    else if (event === 'token') events.push({ type: 'token', data: data as never });
    else if (event === 'done') events.push({ type: 'done', data: data as never });
    else if (event === 'error') events.push({ type: 'error', data: data as never });
  }
  return { events, rest };
}

async function errorFromResponse(response: Response): Promise<Error> {
  const text = await response.text().catch(() => '');
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    const detail = parsed.detail as unknown;
    if (typeof detail === 'string' && detail) return new Error(detail);
    if (detail && typeof detail === 'object') {
      const message = (detail as Record<string, unknown>).error;
      if (typeof message === 'string' && message) return new Error(message);
    }
    const error = parsed.error as unknown;
    if (error && typeof error === 'object') {
      const message = (error as Record<string, unknown>).message;
      if (typeof message === 'string' && message) return new Error(message);
    }
    if (typeof parsed.message === 'string') return new Error(parsed.message);
  } catch {
    // fall through to the status line
  }
  return new Error(text.trim() || `Chat request failed (${response.status})`);
}

/**
 * Send one chat turn and stream the workflow's reply.
 * Resolves with the `done` payload; throws on transport or workflow errors.
 */
export async function streamWorkflowChat(
  request: ChatRequest,
  options: {
    signal?: AbortSignal;
    onEvent?: (event: ChatStreamEvent) => void;
    fetcher?: ChatFetcher;
  } = {},
): Promise<ChatStreamDone> {
  const fetchImpl = options.fetcher ?? fetch;
  const token = csrfToken();
  const response = await fetchImpl(chatEndpoint(request.orgId, request.workflowId), {
    method: 'POST',
    credentials: 'include',
    signal: options.signal,
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(request.orgId ? { 'X-Organization-ID': request.orgId } : {}),
      ...(token ? { 'X-CSRF-Token': token } : {}),
    },
    body: JSON.stringify({
      message: request.message,
      session_id: request.sessionId,
      history: request.history,
      definition: request.definition ?? null,
      files: (request.files ?? []).map((file) => ({
        name: file.name,
        mime_type: file.mimeType,
        data_url: file.dataUrl,
      })),
    }),
  });

  if (!response.ok) throw await errorFromResponse(response);
  if (!response.body) throw new Error('This browser cannot read streamed replies.');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // Held in an object so the closure assignment survives control-flow analysis.
  const outcome: { done: ChatStreamDone | null } = { done: null };
  let finished = false;

  const handle = (event: ChatStreamEvent) => {
    options.onEvent?.(event);
    if (event.type === 'done') outcome.done = event.data;
    if (event.type === 'error') throw new Error(event.data.message || 'The workflow chat failed.');
  };

  while (!finished) {
    const chunk = await reader.read();
    if (chunk.value) buffer += decoder.decode(chunk.value, { stream: true });
    if (chunk.done) {
      buffer += decoder.decode();
      finished = true;
      buffer += '\n\n';
    }
    const parsed = parseSseBuffer(buffer);
    buffer = finished ? '' : parsed.rest;
    for (const event of parsed.events) handle(event);
  }

  if (!outcome.done) throw new Error('The chat stream ended before the workflow replied.');
  return outcome.done;
}
