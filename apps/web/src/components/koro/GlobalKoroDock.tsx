'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import {
  ChevronRight,
  History,
  Loader2,
  Maximize2,
  MessageSquarePlus,
  Plus,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import {
  AssistantComposer,
  type ComposerMode,
  type ComposerSubmit,
} from '@/components/assistant/AssistantComposer';
import { useCredentials } from '@/features/integrations/integrations-api';
import { useWorkflows } from '@/features/workflows/workflows-api';
import { withAttachments } from '@/features/assistant/attachments';
import {
  buildMentionResources,
  loadSavedPrompts,
} from '@/features/assistant/resources';
import { toUserMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useKoroChatbox } from '@/context/KoroContext';
import {
  useAssistantConversation,
  useAssistantConversations,
  useDeleteAssistantConversation,
  useKoroStatus,
} from '@/features/koro/koro-api';
import { useChatModelOverride } from '@/features/koro/use-chat-model';
import { useOrganization } from '@/context/OrganizationContext';
import { streamKoroChat } from '@/features/koro/chat-stream';
import { buildKoroContext } from '@/features/koro/context';
import {
  AssistantStepsCard,
  type DisplayAction,
} from '@/components/assistant/AssistantActionCard';

type Entry = {
  role: 'user' | 'assistant';
  content: string;
  source?: string;
  actions?: DisplayAction[];
};

const SYSTEM_PROMPTS = [
  'Build a workflow that triages support emails',
  'Create an agent that reviews invoices',
  'Every morning, post a digest to Slack',
  'Summarize my last workflow run',
];

const WORKFLOW_PROMPTS = [
  'Explain this workflow',
  'How should I add error handling here?',
  'What could make this workflow fail?',
  'Suggest one improvement to this flow',
];

/**
 * Global Koro chatbox: the same copilot and saved transcripts as /assistant,
 * available as a drawer on every page (top-bar button or Cmd/Ctrl+J).
 * Read-only about the open canvas — graph edits stay in the studio panel.
 */
export function GlobalKoroDock() {
  const { open, closeKoro, prompt, consumePrompt, scope } = useKoroChatbox();
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = React.useState<string | null>(null);
  // Reset the model override only when the user switches chats.
  const [chatEpoch, setChatEpoch] = React.useState(0);
  const { override, setOverride } = useChatModelOverride(String(chatEpoch));
  const [entries, setEntries] = React.useState<Entry[]>([]);
  const [draft, setDraft] = React.useState('');
  const [mode, setMode] = React.useState<ComposerMode>('build');
  const [error, setError] = React.useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  const credentials = useCredentials();
  const workflows = useWorkflows({ pageSize: 50 });

  const status = useKoroStatus(open);
  const conversations = useAssistantConversations(open);
  const detail = useAssistantConversation(open ? activeId : null);
  const { currentOrgId } = useOrganization();
  const [busy, setBusy] = React.useState(false);
  const remove = useDeleteAssistantConversation();

  const ready = status.data?.ready;
  const isWorkflowScope = !!scope?.workflowId;
  const quickPrompts = React.useMemo(
    () => (isWorkflowScope ? WORKFLOW_PROMPTS : SYSTEM_PROMPTS),
    [isWorkflowScope],
  );

  // Replay the selected transcript.
  React.useEffect(() => {
    if (!activeId) return;
    if (!detail.data || detail.data.id !== activeId) return;
    setEntries(
      detail.data.messages.map((message) => ({
        role: message.role === 'user' ? 'user' : 'assistant',
        content: message.content,
        source: message.source ?? undefined,
        actions: message.actions,
      })),
    );
  }, [activeId, detail.data]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeKoro();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, closeKoro]);

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [entries, busy, historyOpen]);

  const send = React.useCallback(
    async (payload: ComposerSubmit) => {
      const message = payload.text.trim();
      if (!message || busy || !currentOrgId) return;
      const outgoing = withAttachments(message, payload.attachments);
      const history = entries.slice(-6).map((entry) => ({
        role: entry.role,
        content: entry.content,
      }));
      setEntries((prev) => [
        ...prev,
        { role: 'user', content: message },
        { role: 'assistant', content: '' },
      ]);
      setDraft('');
      setError(null);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const result = await streamKoroChat(
          currentOrgId,
          {
            message: outgoing,
            history,
            persist: true,
            conversation_id: activeId ?? undefined,
            model: override?.model,
            base_url: override?.base_url,
            context: {
              ...buildKoroContext(
                scope,
                typeof window !== 'undefined' ? window.location.pathname : undefined,
              ),
              mode,
            },
          },
          {
            signal: controller.signal,
            onDelta: (text) =>
              setEntries((prev) => {
                const next = [...prev];
                const last = next.length - 1;
                if (last < 0 || next[last].role !== 'assistant') return prev;
                next[last] = { ...next[last], content: next[last].content + text };
                return next;
              }),
          },
        );
        setEntries((prev) => {
          const next = [...prev];
          const last = next.length - 1;
          if (last >= 0) {
            next[last] = {
              role: 'assistant',
              content: result.reply,
              source: result.source,
              actions: result.actions,
            };
          }
          return next;
        });
        if (!activeId && result.conversation_id) setActiveId(result.conversation_id);
        void queryClient.invalidateQueries({ queryKey: ['koro', 'conversations'] });
        void queryClient.invalidateQueries({ queryKey: ['koro', 'conversation'] });
        void queryClient.invalidateQueries({ queryKey: ['workflows'] });
        void queryClient.invalidateQueries({ queryKey: ['agents'] });
        void queryClient.invalidateQueries({ queryKey: ['runs'] });
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return;
        const text = toUserMessage(e);
        setError(text);
        setEntries((prev) => {
          const next = [...prev];
          if (next.length > 0 && next[next.length - 1].role === 'assistant') {
            next[next.length - 1] = { role: 'assistant', content: text };
            return next;
          }
          return [...prev, { role: 'assistant', content: text }];
        });
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [activeId, busy, currentOrgId, entries, mode, override, queryClient, scope],
  );

  // "Ask Koro to fix" opens the dock with a prompt that sends itself.
  const sendRef = React.useRef(send);
  sendRef.current = send;
  React.useEffect(() => {
    if (!open || !prompt) return;
    const queued = prompt;
    consumePrompt();
    setHistoryOpen(false);
    void sendRef.current({ text: queued, attachments: [] });
  }, [open, prompt, consumePrompt]);

  const resources = React.useMemo(
    () =>
      buildMentionResources({
        workflows: (workflows.items ?? []).map((workflow) => ({
          id: workflow.id,
          name: workflow.name,
          status: workflow.status,
        })),
        credentials: credentials.credentials.map((credential) => ({
          id: credential.id,
          name: credential.name,
          provider: credential.provider,
        })),
        prompts: quickPrompts,
        savedPrompts: loadSavedPrompts(),
      }),
    [workflows.items, credentials.credentials, quickPrompts],
  );

  const startNewChat = () => {
    setActiveId(null);
    setEntries([]);
    setDraft('');
    setError(null);
    setChatEpoch((epoch) => epoch + 1);
    setHistoryOpen(false);
  };

  const openConversation = (id: string) => {
    setActiveId(id);
    setEntries([]);
    setError(null);
    setChatEpoch((epoch) => epoch + 1);
    setHistoryOpen(false);
  };

  const deleteConversation = async (id: string) => {
    try {
      await remove.mutateAsync(id);
      if (id === activeId) startNewChat();
      void queryClient.invalidateQueries({ queryKey: ['koro', 'conversation'] });
    } catch (e) {
      setError(toUserMessage(e));
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[65]" role="presentation">
      <div className="absolute inset-0 bg-black/30" onClick={closeKoro} aria-hidden />
      <aside
        role="dialog"
        aria-modal="false"
        aria-label="Koro assistant"
        className="pointer-events-auto absolute inset-y-0 right-0 flex w-full max-w-[460px] flex-col border-l border-border bg-card shadow-2xl"
      >
        <header className="flex shrink-0 items-center gap-2 border-b border-border/70 bg-gradient-to-r from-primary/[0.06] to-transparent px-3 py-2.5">
          <span className="oa-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-white shadow-sm">
            <Sparkles className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold leading-tight">
              Koro
              {isWorkflowScope && (
                <span className="truncate rounded-full border border-border/70 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                  {scope?.workflowName ?? 'workflow'}
                </span>
              )}
            </h2>
            <p className="oa-caption flex items-center gap-1.5 truncate">
              <span
                className={cn(
                  'h-1.5 w-1.5 shrink-0 rounded-full',
                  ready ? 'bg-emerald-500' : 'bg-amber-500',
                )}
                aria-hidden
              />
              {status.isLoading
                ? 'Checking the model…'
                : ready
                  ? `${status.data?.model} · ${status.data?.provider}`
                  : 'Command mode — no model needed'}
            </p>
          </div>
          <button
            onClick={startNewChat}
            aria-label="New chat"
            title="New chat"
            className="oa-icon-button"
          >
            <MessageSquarePlus className="h-4 w-4" />
          </button>
          <button
            onClick={() => setHistoryOpen((value) => !value)}
            aria-label="Chat history"
            title="Chat history"
            aria-expanded={historyOpen}
            className={cn('oa-icon-button', historyOpen && 'bg-accent text-foreground')}
          >
            <History className="h-4 w-4" />
          </button>
          <Link href="/assistant" aria-label="Open the full assistant" title="Open full assistant" className="oa-icon-button">
            <Maximize2 className="h-4 w-4" />
          </Link>
          <button onClick={closeKoro} aria-label="Close assistant" className="oa-icon-button">
            <X className="h-4 w-4" />
          </button>
        </header>

        {historyOpen && (
          <div className="oa-scroll max-h-[45%] shrink-0 overflow-y-auto border-b border-border/70 bg-background/60 p-2">
            <div className="mb-1 flex items-center justify-between px-1">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                Saved chats
              </p>
              <button
                onClick={startNewChat}
                className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
              >
                <Plus className="h-3 w-3" aria-hidden /> New
              </button>
            </div>
            {conversations.isLoading ? (
              <p className="px-1 py-2 text-xs text-muted-foreground">Loading chats…</p>
            ) : (conversations.data ?? []).length === 0 ? (
              <p className="px-1 py-2 text-xs text-muted-foreground">
                No chats yet. They are saved automatically.
              </p>
            ) : (
              <ul className="space-y-0.5">
                {(conversations.data ?? []).map((conversation) => (
                  <li
                    key={conversation.id}
                    className={cn(
                      'group flex items-center gap-1 rounded-lg pr-1',
                      conversation.id === activeId ? 'bg-primary/10' : 'hover:bg-accent/70',
                    )}
                  >
                    <button
                      onClick={() => openConversation(conversation.id)}
                      className="min-w-0 flex-1 px-2 py-1.5 text-left"
                    >
                      <span className="block truncate text-[12.5px]">{conversation.title}</span>
                      <span className="oa-caption block truncate">
                        {conversation.message_count} message
                        {conversation.message_count === 1 ? '' : 's'}
                      </span>
                    </button>
                    <button
                      onClick={() => void deleteConversation(conversation.id)}
                      aria-label={`Delete chat “${conversation.title}”`}
                      className="rounded p-1 text-muted-foreground opacity-0 hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                    <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/50" aria-hidden />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div ref={scrollRef} className="oa-scroll min-h-0 flex-1 overflow-y-auto p-3">
          {entries.length === 0 ? (
            <div className="space-y-3">
              <div className="rounded-xl border border-border/60 bg-background/60 p-3">
                <p className="text-sm font-medium">
                  {isWorkflowScope ? `Working on “${scope?.workflowName}”` : "Hi, I'm Koro."}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {isWorkflowScope
                    ? 'I can see this workflow’s steps, validation state and failed runs. Ask me to explain it, review it, or debug a failure — canvas edits stay in the studio assistant.'
                    : 'Ask me to build a workflow, create an agent, explain a run or debug a failure. Every chat is saved.'}
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {quickPrompts.map((quick) => (
                  <button
                    key={quick}
                    onClick={() => void send({ text: quick, attachments: [] })}
                    disabled={busy}
                    className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-left text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
                  >
                    {quick}
                  </button>
                ))}
              </div>
              {status.data && !status.data.ready && (
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-2.5 py-2 text-[11px] text-muted-foreground">
                  {status.data.install_hint}{' '}
                  <Link href="/settings/models" className="font-medium text-primary hover:underline">
                    Connect a model
                  </Link>
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              {detail.isLoading && activeId && (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading transcript…
                </p>
              )}
              {entries.map((entry, index) => (
                <div
                  key={index}
                  className={cn(
                    'flex flex-col gap-1.5',
                    entry.role === 'user' ? 'items-end' : 'items-start',
                  )}
                >
                  <div
                    className={cn(
                      'max-w-[92%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm leading-6',
                      entry.role === 'user'
                        ? 'rounded-br-md bg-primary text-primary-foreground'
                        : 'rounded-bl-md border border-border/70 bg-background',
                    )}
                  >
                    {entry.content}
                  </div>
                  {entry.actions && entry.actions.length > 0 && (
                    <div className="w-full max-w-[92%]">
                      <AssistantStepsCard actions={entry.actions} />
                    </div>
                  )}
                  {entry.role === 'assistant' && entry.source && (
                    <span className="px-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {entry.source === 'commands'
                        ? 'offline commands'
                        : status.data?.provider ?? 'model'}
                    </span>
                  )}
                </div>
              ))}
              {busy && (
                <p className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Working on it…
                </p>
              )}
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border/70 bg-card p-2.5">
          {error && (
            <p className="mb-2 rounded-lg border border-destructive/30 bg-destructive/[0.06] px-2.5 py-1.5 text-xs text-destructive" role="alert">
              {error}
            </p>
          )}
          <AssistantComposer
            value={draft}
            onChange={setDraft}
            onSend={(payload) => void send(payload)}
            onStop={() => abortRef.current?.abort()}
            busy={busy}
            resources={resources}
            starters={quickPrompts}
            mode={mode}
            onModeChange={setMode}
            modelOverride={override}
            onModelOverride={setOverride}
            placeholder={
              isWorkflowScope
                ? `Ask about “${scope?.workflowName ?? 'this workflow'}” or request a change`
                : 'Tell me what to build or ask a question'
            }
            footer={
              <p className="oa-caption mt-1.5 flex items-center gap-1.5 px-1">
                <kbd className="oa-kbd">Ctrl J</kbd>
                toggles Koro · {mode === 'build' ? 'build mode' : 'plan mode'} · actions are
                permission-checked
              </p>
            }
          />
        </div>
      </aside>
    </div>
  );
}
