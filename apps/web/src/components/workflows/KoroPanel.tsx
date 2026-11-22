'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  ArrowUp,
  Bot,
  Check,
  ChevronLeft,
  Download,
  Loader2,
  Radio,
  SlidersHorizontal,
  Sparkles,
  Wand2,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toUserMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { ModelManager } from '@/components/koro/ModelManager';
import {
  useKoroChat,
  useKoroStatus,
  useUpdateKoroSettings,
  type KoroAction,
  type KoroChatResult,
} from '@/features/koro/koro-api';
import type { WorkflowDefinition } from '@/features/workflows/types';

type Entry = {
  role: 'user' | 'assistant';
  content: string;
  actions?: KoroChatResult['actions'];
  source?: string;
};

type View = 'chat' | 'models' | 'settings';

const QUICK_PROMPTS_EDITING = [
  'Add a step that sends a Slack message',
  'Summarize this workflow',
  'Why did the last run fail?',
  'Run this workflow',
  'Add an AI step that classifies the input',
  'Publish this workflow',
];

const QUICK_PROMPTS_EMPTY = [
  'Build a workflow that triages support emails',
  'Create an agent that summarizes invoices',
  'Every morning, post a digest to Slack',
  'When a new lead arrives, enrich and notify sales',
  'Build an automation that logs form submissions',
];

function isWorkflowDefinition(value: unknown): value is WorkflowDefinition {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<WorkflowDefinition>;
  return (
    Array.isArray(candidate.triggers) &&
    Array.isArray(candidate.nodes) &&
    Array.isArray(candidate.edges)
  );
}

export function KoroPanel({
  open,
  onClose,
  workflowId,
  workflowName,
  currentDefinition,
  onApply,
  onResult,
}: {
  open: boolean;
  onClose: () => void;
  workflowId?: string;
  workflowName?: string;
  /** The unsaved canvas, so edits start from what the user sees. */
  currentDefinition?: WorkflowDefinition;
  /** Apply a generated definition to the canvas (undoable, not persisted). */
  onApply?: (definition: WorkflowDefinition, name?: string) => void;
  /** Called after every completed turn so the builder can refresh. */
  onResult?: (result: KoroChatResult) => void;
}) {
  const [entries, setEntries] = React.useState<Entry[]>([]);
  const [text, setText] = React.useState('');
  const [view, setView] = React.useState<View>('chat');
  const [applied, setApplied] = React.useState<Set<number>>(new Set());
  const [baseUrl, setBaseUrl] = React.useState('');
  const [model, setModel] = React.useState('');
  const [apiKey, setApiKey] = React.useState('');
  const [clearKey, setClearKey] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const status = useKoroStatus(open);
  const chat = useKoroChat();
  const saveSettings = useUpdateKoroSettings();

  React.useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => inputRef.current?.focus(), 80);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [entries, chat.isPending, view]);

  if (!open) return null;

  const send = async (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || chat.isPending) return;
    const history = entries
      .slice(-6)
      .map((entry) => ({ role: entry.role, content: entry.content }));
    setEntries((prev) => [...prev, { role: 'user', content: trimmed }]);
    setText('');
    try {
      const hasCanvas =
        !!currentDefinition &&
        (currentDefinition.triggers.length > 0 || currentDefinition.nodes.length > 0);
      const result = await chat.mutateAsync({
        message: trimmed,
        workflow_id: workflowId,
        history,
        definition: hasCanvas ? (currentDefinition as unknown as Record<string, unknown>) : undefined,
        preview: true,
      });
      setEntries((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: result.reply,
          actions: result.actions,
          source: result.source,
        },
      ]);
      onResult?.(result);
    } catch (error) {
      setEntries((prev) => [
        ...prev,
        { role: 'assistant', content: toUserMessage(error) },
      ]);
    }
  };

  const openSettings = () => {
    setBaseUrl(status.data?.base_url ?? '');
    setModel(status.data?.model ?? '');
    setApiKey('');
    setClearKey(false);
    saveSettings.reset();
    setView('settings');
  };

  const submitSettings = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!baseUrl.trim() || !model.trim()) return;
    try {
      await saveSettings.mutateAsync({
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
        clear_api_key: clearKey,
      });
      setView('chat');
      void status.refetch();
    } catch {
      /* error rendered below */
    }
  };

  const apply = (index: number, action: KoroAction) => {
    const payload = action.data as
      | { definition?: unknown; name?: string }
      | null
      | undefined;
    if (!isWorkflowDefinition(payload?.definition)) return;
    onApply?.(payload.definition, payload?.name);
    setApplied((prev) => new Set(prev).add(index));
  };

  const ready = status.data?.ready;
  const isOllama = status.data?.provider === 'Ollama';
  const local = status.data?.local ?? true;
  const prompts = workflowId ? QUICK_PROMPTS_EDITING : QUICK_PROMPTS_EMPTY;

  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-end justify-end p-2 sm:p-4">
      <aside
        role="dialog"
        aria-modal="false"
        aria-label="AI assistant"
        className="pointer-events-auto flex h-[min(680px,calc(100vh-1rem))] w-full max-w-[440px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_-20px_rgb(15_23_42/0.45)] sm:h-[min(680px,calc(100vh-2rem))]"
      >
        {/* Header */}
        <header className="flex shrink-0 items-center gap-2.5 border-b border-border/70 bg-gradient-to-r from-primary/[0.07] to-transparent px-3 py-2.5">
          <span className="oa-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-white shadow-sm">
            <Sparkles className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold leading-tight">
              AI Assistant
              <span className="rounded-full border border-border/70 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                {local ? 'local' : 'remote'}
              </span>
            </h2>
            <p className="oa-caption truncate">
              {ready ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />
                  {status.data?.model} · {status.data?.provider}
                </span>
              ) : status.isLoading ? (
                'Checking model endpoint…'
              ) : (
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden />
                  Command mode — no model needed
                </span>
              )}
            </p>
          </div>
          <button
            onClick={() => (view === 'chat' ? setView('models') : setView('chat'))}
            aria-label="Local models"
            aria-pressed={view === 'models'}
            title="Local models"
            className={cn(
              'rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground',
              view === 'models' && 'bg-accent text-foreground',
            )}
          >
            <Download className="h-4 w-4" />
          </button>
          <button
            onClick={openSettings}
            aria-label="Endpoint settings"
            aria-pressed={view === 'settings'}
            title="Endpoint settings"
            className={cn(
              'rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground',
              view === 'settings' && 'bg-accent text-foreground',
            )}
          >
            <SlidersHorizontal className="h-4 w-4" />
          </button>
          <button
            onClick={onClose}
            aria-label="Close assistant"
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div ref={scrollRef} className="oa-scroll min-h-0 flex-1 overflow-y-auto">
          {view === 'models' && (
            <div>
              <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
                <button
                  onClick={() => setView('chat')}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ChevronLeft className="h-3.5 w-3.5" /> Back to chat
                </button>
                <Link
                  href="/settings/models"
                  className="ml-auto text-[11px] font-medium text-primary hover:underline"
                  onClick={onClose}
                >
                  Open full page
                </Link>
              </div>
              <ModelManager compact onActivated={() => void status.refetch()} />
            </div>
          )}

          {view === 'settings' && (
            <form onSubmit={submitSettings} className="space-y-3 p-4">
              <div>
                <h3 className="text-sm font-semibold">Model endpoint</h3>
                <p className="oa-caption mt-0.5">
                  Any OpenAI-compatible endpoint works. Use a local runtime for a fully
                  offline setup, or a hosted API if you prefer.
                </p>
              </div>

              <label className="block space-y-1">
                <span className="text-xs font-medium">Base URL</span>
                <input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="http://127.0.0.1:11434/v1"
                  spellCheck={false}
                  className="w-full rounded-lg border bg-background px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary"
                />
                <span className="oa-caption block">
                  Must include the API prefix, usually ending in /v1.
                </span>
              </label>

              <label className="block space-y-1">
                <span className="text-xs font-medium">Model</span>
                <input
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder="qwen2.5:0.5b-instruct"
                  spellCheck={false}
                  className="w-full rounded-lg border bg-background px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary"
                  list="koro-model-options"
                />
                {status.data && status.data.models.length > 0 && (
                  <datalist id="koro-model-options">
                    {status.data.models.map((name) => (
                      <option key={name} value={name} />
                    ))}
                  </datalist>
                )}
              </label>

              <label className="block space-y-1">
                <span className="text-xs font-medium">API key</span>
                <input
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  type="password"
                  autoComplete="off"
                  placeholder={
                    status.data?.api_key_set ? 'Stored — leave blank to keep' : 'Optional'
                  }
                  className="w-full rounded-lg border bg-background px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary"
                />
                <span className="oa-caption block">
                  Encrypted before storage and never shown again. Local servers usually
                  need no key.
                </span>
              </label>

              {status.data?.api_key_set && (
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={clearKey}
                    onChange={(event) => setClearKey(event.target.checked)}
                    className="h-3.5 w-3.5 rounded border-border"
                  />
                  Remove the stored key
                </label>
              )}

              {saveSettings.isError && (
                <p className="text-xs text-destructive" role="alert">
                  {toUserMessage(saveSettings.error)}
                </p>
              )}

              <div className="flex items-center gap-2 pt-1">
                <Button type="submit" size="sm" disabled={saveSettings.isPending}>
                  {saveSettings.isPending ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : null}
                  Save endpoint
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setView('models')}
                >
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  Local models
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setView('chat')}
                >
                  Cancel
                </Button>
              </div>
              <p className="oa-caption">
                Without a reachable model, the assistant still builds, edits, runs and
                publishes workflows from plain commands.
              </p>
            </form>
          )}

          {view === 'chat' && (
            <div className="space-y-3 p-3">
              {status.data && !status.data.ready && (
                <section className="rounded-xl border border-primary/25 bg-primary/[0.04] p-3">
                  <p className="flex items-center gap-1.5 text-sm font-medium">
                    <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden />
                    Run a model locally
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {status.data.install_hint}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button size="sm" onClick={() => setView('models')}>
                      <Download className="mr-1.5 h-3.5 w-3.5" />
                      Download a model
                    </Button>
                    <Button size="sm" variant="outline" onClick={openSettings}>
                      <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5" />
                      Use another endpoint
                    </Button>
                  </div>
                  {isOllama && (
                    <p className="oa-caption mt-2 font-mono">
                      ollama pull {status.data.model}
                    </p>
                  )}
                </section>
              )}

              {entries.length === 0 && (
                <div className="space-y-3">
                  <div className="rounded-xl border border-border/60 bg-background/60 p-3">
                    <p className="text-sm font-medium">
                      Hi, I&apos;m your AI assistant.
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Tell me what to automate and I&apos;ll draft it on the canvas.
                      {workflowName ? ` Currently open: “${workflowName}”.` : ''} I can
                      also create agents, run workflows, explain failures and switch the
                      local model.
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {prompts.map((prompt) => (
                      <button
                        key={prompt}
                        onClick={() => send(prompt)}
                        disabled={chat.isPending}
                        className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-left text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
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
                      'max-w-[92%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm',
                      entry.role === 'user'
                        ? 'rounded-br-md bg-primary text-primary-foreground'
                        : 'rounded-bl-md border border-border/70 bg-background',
                    )}
                  >
                    {entry.content}
                  </div>

                  {entry.actions?.map((action, i) => {
                    if (action.type === 'none') return null;
                    const payload = action.data as
                      | { definition?: unknown; name?: string }
                      | null
                      | undefined;
                    const canApply =
                      action.status === 'ok' &&
                      (action.type === 'build' || action.type === 'edit') &&
                      isWorkflowDefinition(payload?.definition);
                    const wasApplied = applied.has(index);
                    return (
                      <div
                        key={i}
                        className={cn(
                          'max-w-[92%] rounded-lg border px-2.5 py-2 text-[11px]',
                          action.status === 'ok'
                            ? 'border-emerald-500/25 bg-emerald-500/[0.06] text-emerald-700 dark:text-emerald-300'
                            : 'border-destructive/30 bg-destructive/[0.06] text-destructive',
                        )}
                      >
                        <p className="flex items-start gap-2">
                          <Radio className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                          <span>{action.detail}</span>
                        </p>
                        {(canApply || action.type === 'create_agent') && (
                          <div className="mt-2 flex flex-wrap items-center gap-1.5 pl-5">
                            {canApply && onApply && (
                              <Button
                                size="sm"
                                className="h-7 px-2 text-[11px]"
                                disabled={wasApplied}
                                onClick={() => apply(index, action)}
                              >
                                {wasApplied ? (
                                  <Check className="mr-1 h-3 w-3" />
                                ) : (
                                  <Wand2 className="mr-1 h-3 w-3" />
                                )}
                                {wasApplied ? 'Applied to canvas' : 'Apply to canvas'}
                              </Button>
                            )}
                            {action.type === 'create_agent' && (
                              <Link
                                href="/agents"
                                onClick={onClose}
                                className="inline-flex h-7 items-center gap-1 rounded-md border border-emerald-500/30 px-2 text-[11px] font-medium hover:bg-emerald-500/10"
                              >
                                <Bot className="h-3 w-3" /> Open agents
                              </Link>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {entry.role === 'assistant' && entry.source === 'model' && (
                    <span className="px-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {status.data?.provider ?? 'model'}
                    </span>
                  )}
                </div>
              ))}

              {chat.isPending && (
                <p
                  className="flex items-center gap-2 text-xs text-muted-foreground"
                  aria-live="polite"
                >
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Working on it…
                </p>
              )}
            </div>
          )}
        </div>

        {view === 'chat' && (
          <form
            className="shrink-0 border-t border-border/70 bg-card p-2.5"
            onSubmit={(event) => {
              event.preventDefault();
              void send(text);
            }}
          >
            <div className="flex items-end gap-2 rounded-xl border bg-background p-1.5 focus-within:border-primary">
              <input
                ref={inputRef}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="Describe what to build or change…"
                aria-label="Message the AI assistant"
                className="min-w-0 flex-1 bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
              />
              <Button
                type="submit"
                size="sm"
                disabled={!text.trim() || chat.isPending}
                aria-label="Send message"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
            </div>
            <p className="oa-caption mt-1.5 flex items-center gap-1.5 px-1">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  ready ? 'bg-emerald-500' : 'bg-amber-500',
                )}
                aria-hidden
              />
              {local
                ? 'Runs on this machine · actions are permission-checked'
                : 'Uses your configured endpoint · actions are permission-checked'}
            </p>
          </form>
        )}
      </aside>
    </div>
  );
}
