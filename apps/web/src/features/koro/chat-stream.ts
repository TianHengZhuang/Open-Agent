'use client';

// SSE client for the assistant thread. `api` cannot stream, so this mirrors its
// headers (cookies, org, CSRF) and parses `data:` events into deltas + a final
// result.

import { API_BASE, ApiError } from '@/lib/api';
import type { KoroChatResult, KoroMessage } from './koro-api';

export interface ChatStreamInput {
  message: string;
  workflow_id?: string;
  history?: KoroMessage[];
  input?: Record<string, unknown>;
  definition?: Record<string, unknown>;
  preview?: boolean;
  conversation_id?: string;
  persist?: boolean;
  context?: Record<string, unknown>;
  model?: string;
  base_url?: string;
}

export interface ChatStreamHandlers {
  /** Called with each incremental slice of the reply. */
  onDelta?: (text: string) => void;
  signal?: AbortSignal;
}

function csrfToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let code = 'UNKNOWN_ERROR';
  try {
    const body = (await res.json()) as {
      error?: { message?: string; code?: string };
      detail?: string;
    };
    if (body.error?.message) message = body.error.message;
    else if (typeof body.detail === 'string') message = body.detail;
    if (body.error?.code) code = body.error.code;
  } catch {
    // Keep the status-based message.
  }
  return new ApiError(message, { status: res.status, code });
}

/**
 * Send one assistant turn and stream the answer. Resolves with the same result
 * shape as the non-streaming endpoint (actions, conversation id).
 */
export async function streamKoroChat(
  organizationId: string,
  input: ChatStreamInput,
  handlers: ChatStreamHandlers = {},
): Promise<KoroChatResult> {
  const token = csrfToken();
  const response = await fetch(
    `${API_BASE}/organizations/${organizationId}/copilot/chat/stream`,
    {
      method: 'POST',
      credentials: 'include',
      signal: handlers.signal,
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        'X-Organization-ID': organizationId,
        ...(token ? { 'X-CSRF-Token': token } : {}),
      },
      body: JSON.stringify(input),
    },
  );

  if (!response.ok) throw await errorFrom(response);
  if (!response.body) throw new ApiError('The reply stream was empty.', { status: 0, code: 'NETWORK_ERROR' });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let final: KoroChatResult | null = null;

  const handleBlock = (block: string) => {
    for (const line of block.split('\n')) {
      if (!line.startsWith('data:')) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      let event: { type?: string; text?: string; result?: KoroChatResult };
      try {
        event = JSON.parse(raw);
      } catch {
        continue;
      }
      if (event.type === 'delta' && typeof event.text === 'string') {
        handlers.onDelta?.(event.text);
      } else if (event.type === 'final' && event.result) {
        final = event.result;
      }
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        handleBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf('\n\n');
      }
    }
    if (buffer.trim()) handleBlock(buffer);
  } finally {
    reader.releaseLock();
  }

  if (!final) {
    throw new ApiError('The reply stream ended before the result arrived.', {
      status: 0,
      code: 'NETWORK_ERROR',
    });
  }
  return final;
}
