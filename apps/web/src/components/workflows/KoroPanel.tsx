'use client';

import * as React from 'react';
import {
  ArrowUp,
  Check,
  Copy,
  Loader2,
  Radio,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toUserMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  useKoroChat,
  useKoroStatus,
  useUpdateKoroSettings,
  type KoroChatResult,
} from '@/features/koro/koro-api';

type Entry = {
  role: 'user' | 'assistant';
  content: string;
  actions?: KoroChatResult['actions'];
  source?: string;
};

const QUICK_PROMPTS = [
  'Run this workflow',
  'Publish this workflow',
  'Summarize the last run',
  'List my workflows',
  'Build an automation for new leads',
  'Explain this workflow',
  'Why did the last run fail?',
  'Create a workflow called Daily digest',
];

export function KoroPanel({
  open,
  onClose,
  workflowId,
  workflowName,
  onResult,
}: {
  open: boolean;
  onClose: () => void;
  workflowId?: string;
  workflowName?: string;
  /** Called after every completed turn so the builder can refresh. */
  onResult?: (result: KoroChatResult) => void;
}) {
  const [entries, setEntries] = React.useState<Entry[]>([]);
  const [text, setText] = React.useState('');
  const [copied, setCopied] = React.useState(false);
  const [showSettings, setShowSettings] = React.useState(false);
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
  }, [entries, chat.isPending, showSettings]);

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
      const result = await chat.mutateAsync({
        message: trimmed,
        workflow_id: workflowId,
        history,
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

  const copyCommand = async () => {
    const modelName = status.data?.model ?? 'qwen2.5:0.5b-instruct';
    try {
      await navigator.clipboard.writeText(`ollama pull ${modelName}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const openSettings = () => {
    setBaseUrl(status.data?.base_url ?? '');
    setModel(status.data?.model ?? '');
    setApiKey('');
    setClearKey(false);
    saveSettings.reset();
    setShowSettings(true);
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
      setShowSettings(false);
      void status.refetch();
    } catch {
      /* error rendered below */
    }
  };

  const ready = status.data?.ready;
  const isOllama = status.data?.provider === 'Ollama';
  const local = status.data?.local ?? true;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Koro automation copilot"
        className="animate-fade-in relative flex h-full w-full max-w-[420px] flex-col border-l bg-background shadow-2xl"
      >
        <header className="flex items-start gap-3 border-b border-border/70 px-4 py-3">
          <span className="oa-brand flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white shadow-sm">
            <Sparkles className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-sm font-semibold leading-tight">
              Koro
              <span className="rounded-full border border-border/70 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
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
            onClick={openSettings}
            aria-label="Koro model settings"
            aria-pressed={showSettings}
            className={cn(
              'rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground',
              showSettings && 'bg-accent text-foreground',
            )}
          >
            <SlidersHorizontal className="h-4 w-4" />
          </button>
          <button
            onClick={onClose}
            aria-label="Close Koro"
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div ref={scrollRef} className="oa-scroll flex-1 space-y-3 overflow-y-auto p-4">
          {showSettings ? (
            <form onSubmit={submitSettings} className="space-y-3">
              <div>
                <h3 className="text-sm font-semibold">Model settings</h3>
                <p className="oa-caption mt-0.5">
                  Any OpenAI-compatible endpoint works — Ollama or LM Studio on this
                  machine, or a hosted API such as OpenAI, OpenRouter or Groq.
                </p>
              </div>

              <label className="block space-y-1">
                <span className="text-xs font-medium">Base URL</span>
                <input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="http://127.0.0.1:11434/v1"
                  spellCheck={false}
                  className="w-full rounded-lg border bg-card px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary"
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
                  className="w-full rounded-lg border bg-card px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary"
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
                  className="w-full rounded-lg border bg-card px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary"
                />
                <span className="oa-caption block">
                  Encrypted before storage and never shown again. Local servers
                  usually need no key.
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
                  Save
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowSettings(false)}
                >
                  Cancel
                </Button>
              </div>
              <p className="oa-caption">
                Without a reachable model, Koro still runs, publishes and creates
                workflows from plain commands.
              </p>
            </form>
          ) : (
            <>
              {status.data && !status.data.ready && (
                <section className="rounded-xl border border-primary/25 bg-primary/[0.04] p-3">
                  <p className="flex items-center gap-1.5 text-sm font-medium">
                    <Terminal className="h-3.5 w-3.5 text-primary" aria-hidden />
                    Connect an AI model
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{status.data.install_hint}</p>
                  {isOllama && !status.data.api_key_set && (
                    <div className="mt-2 flex items-center gap-2">
                      <code className="min-w-0 flex-1 truncate rounded-lg border bg-background px-2 py-1.5 font-mono text-[11px]">
                        ollama pull {status.data.model}
                      </code>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={copyCommand}
                        aria-label="Copy model install command"
                      >
                        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                      </Button>
                    </div>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-2"
                    onClick={openSettings}
                  >
                    <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5" />
                    Model settings
                  </Button>
                </section>
              )}

              {entries.length === 0 && (
                <div className="space-y-3">
                  <p className="text-sm text-muted-foreground">
                    I can drive this workflow end to end. {workflowName ? `Currently open: “${workflowName}”.` : ''}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {QUICK_PROMPTS.map((prompt) => (
                      <button
                        key={prompt}
                        onClick={() => send(prompt)}
                        disabled={chat.isPending}
                        className="rounded-full border border-border/70 bg-card px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
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
                  className={cn('flex flex-col gap-1.5', entry.role === 'user' ? 'items-end' : 'items-start')}
                >
                  <div
                    className={cn(
                      'max-w-[92%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm',
                      entry.role === 'user'
                        ? 'rounded-br-md bg-primary text-primary-foreground'
                        : 'rounded-bl-md border border-border/70 bg-card',
                    )}
                  >
                    {entry.content}
                  </div>
                  {entry.actions?.map((action, i) =>
                    action.type === 'none' ? null : (
                      <div
                        key={i}
                        className={cn(
                          'flex max-w-[92%] items-start gap-2 rounded-lg border px-2.5 py-1.5 text-[11px]',
                          action.status === 'ok'
                            ? 'border-emerald-500/25 bg-emerald-500/[0.06] text-emerald-700 dark:text-emerald-300'
                            : 'border-destructive/30 bg-destructive/[0.06] text-destructive',
                        )}
                      >
                        <Radio className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                        <span>{action.detail}</span>
                      </div>
                    ),
                  )}
                  {entry.role === 'assistant' && entry.source === 'model' && (
                    <span className="px-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {status.data?.provider ?? 'model'}
                    </span>
                  )}
                </div>
              ))}

              {chat.isPending && (
                <p className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Koro is working…
                </p>
              )}
            </>
          )}
        </div>

        <form
          className="border-t border-border/70 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void send(text);
          }}
        >
          <div className="flex items-end gap-2 rounded-xl border bg-card p-1.5 focus-within:border-primary">
            <input
              ref={inputRef}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Ask Koro to build, run or publish…"
              aria-label="Message Koro"
              className="min-w-0 flex-1 bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
            />
            <Button type="submit" size="sm" disabled={!text.trim() || chat.isPending} aria-label="Send to Koro">
              <ArrowUp className="h-4 w-4" />
            </Button>
          </div>
          <p className="oa-caption mt-1.5 px-1">
            {local
              ? 'Runs on this machine · actions are permission-checked'
              : 'Uses your configured endpoint · actions are permission-checked'}
          </p>
        </form>
      </aside>
    </div>
  );
}
