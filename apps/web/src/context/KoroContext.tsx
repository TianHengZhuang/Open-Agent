'use client';

import * as React from 'react';
import type { KoroScope } from '@/features/koro/context';

interface KoroChatValue {
  open: boolean;
  /** A prompt the dock should send as soon as it opens. */
  prompt: string | null;
  scope: KoroScope | null;
  openKoro: (prompt?: string, scope?: KoroScope) => void;
  closeKoro: () => void;
  consumePrompt: () => void;
}

const KoroChatContext = React.createContext<KoroChatValue | null>(null);

export function KoroProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  const [prompt, setPrompt] = React.useState<string | null>(null);
  const [scope, setScope] = React.useState<KoroScope | null>(null);

  const openKoro = React.useCallback((nextPrompt?: string, nextScope?: KoroScope) => {
    setScope(nextScope ?? null);
    setPrompt(nextPrompt ?? null);
    setOpen(true);
  }, []);

  const closeKoro = React.useCallback(() => setOpen(false), []);
  const consumePrompt = React.useCallback(() => setPrompt(null), []);

  // Cmd/Ctrl+J toggles the chatbox from anywhere in the app.
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'j') {
        event.preventDefault();
        setOpen((current) => !current);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const value = React.useMemo(
    () => ({ open, prompt, scope, openKoro, closeKoro, consumePrompt }),
    [open, prompt, scope, openKoro, closeKoro, consumePrompt],
  );

  return <KoroChatContext.Provider value={value}>{children}</KoroChatContext.Provider>;
}

export function useKoroChatbox(): KoroChatValue {
  const context = React.useContext(KoroChatContext);
  if (!context) {
    throw new Error('useKoroChatbox must be used inside KoroProvider');
  }
  return context;
}
