'use client';

import * as React from 'react';

/** A model choice that applies to the open chat only. */
export interface ChatModelOverride {
  model: string;
  base_url: string;
}

const PREFIX = 'oa:chat-model:';

function storageKey(chatKey: string | null): string {
  return `${PREFIX}${chatKey ?? 'new'}`;
}

function read(key: string): ChatModelOverride | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === 'object' &&
      typeof (parsed as ChatModelOverride).model === 'string' &&
      typeof (parsed as ChatModelOverride).base_url === 'string'
    ) {
      return parsed as ChatModelOverride;
    }
  } catch {
    // Corrupt entry — fall back to the workspace default.
  }
  return null;
}

function write(key: string, value: ChatModelOverride | null): void {
  if (typeof window === 'undefined') return;
  try {
    if (value) window.localStorage.setItem(key, JSON.stringify(value));
    else window.localStorage.removeItem(key);
  } catch {
    // Quota or private mode — the override still works for this session.
  }
}

/**
 * Per-chat model override, stored per chat thread so reopening a chat restores
 * its model. Choosing one never rewrites the organization default.
 */
export function useChatModelOverride(chatKey: string | null) {
  // Start empty so server and client markup match; the stored choice is
  // applied in the effect below.
  const [override, setState] = React.useState<ChatModelOverride | null>(null);

  React.useEffect(() => {
    setState(read(storageKey(chatKey)));
  }, [chatKey]);

  const setOverride = React.useCallback(
    (value: ChatModelOverride | null) => {
      setState(value);
      write(storageKey(chatKey), value);
    },
    [chatKey],
  );

  return { override, setOverride };
}
