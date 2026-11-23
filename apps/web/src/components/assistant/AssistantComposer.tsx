'use client';

import * as React from 'react';
import {
  ArrowUp,
  AtSign,
  FileText,
  Hammer,
  Loader2,
  Map,
  Mic,
  Paperclip,
  Plus,
  Sparkles,
  Square,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { ModelPicker } from '@/components/assistant/ModelPicker';
import { useKoroStatus } from '@/features/koro/koro-api';
import type { ChatModelOverride } from '@/features/koro/use-chat-model';
import { pillLabel, providerForBaseUrl } from '@/lib/ai-models';
import { useSpeechInput } from '@/components/assistant/useSpeechInput';
import {
  activeMention,
  applyMention,
  filterMentions,
  type MentionQuery,
  type MentionResource,
} from '@/features/assistant/mentions';
import {
  MAX_ATTACHMENT_BYTES,
  formatBytes,
  readAttachments,
  type AssistantAttachment,
} from '@/features/assistant/attachments';
import type { RobotVoiceActivity } from '@/features/robot/use-robot-companion';

export type ComposerMode = 'build' | 'plan';

export interface ComposerSubmit {
  text: string;
  attachments: AssistantAttachment[];
}

export interface AssistantComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (payload: ComposerSubmit) => void;
  /** Cancel the in-flight reply; the send button becomes Stop when set. */
  onStop?: () => void;
  busy?: boolean;
  disabled?: boolean;
  placeholder?: string;
  /** Taller composer for the empty assistant page. */
  large?: boolean;
  autoFocus?: boolean;
  resources?: MentionResource[];
  starters?: string[];
  mode?: ComposerMode;
  onModeChange?: (mode: ComposerMode) => void;
  /** Model chosen for this chat only; null means the organization default. */
  modelOverride?: ChatModelOverride | null;
  onModelOverride?: (override: ChatModelOverride | null) => void;
  /** Drives the 3D mascot from the real dictation session. */
  onVoiceActivity?: (activity: RobotVoiceActivity) => void;
  className?: string;
  /** Rendered under the toolbar (status line, hints). */
  footer?: React.ReactNode;
}

/**
 * The assistant prompt composer: auto-growing textarea, attachments, prompt
 * starters, `@` mentions, dictation, build/plan mode and the model switcher.
 */
export function AssistantComposer({
  value,
  onChange,
  onSend,
  onStop,
  busy = false,
  disabled = false,
  placeholder = 'Tell me what to build or ask a question',
  large = false,
  autoFocus = false,
  resources = [],
  starters = [],
  mode = 'build',
  onModeChange,
  modelOverride,
  onModelOverride,
  onVoiceActivity,
  className,
  footer,
}: AssistantComposerProps) {
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);
  const [attachments, setAttachments] = React.useState<AssistantAttachment[]>([]);
  const [fileNote, setFileNote] = React.useState<string | null>(null);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [modelOpen, setModelOpen] = React.useState(false);
  const [caret, setCaret] = React.useState(0);
  const [dismissed, setDismissed] = React.useState(false);
  const [highlight, setHighlight] = React.useState(0);
  const pendingCaret = React.useRef<number | null>(null);
  const voiceBase = React.useRef('');
  const status = useKoroStatus();

  const trimmed = value.trim();
  const sendable = !!trimmed && !busy && !disabled;
  const pillModel = modelOverride?.model ?? (status.data?.ready ? status.data.model : '');
  const pillProvider = modelOverride
    ? providerForBaseUrl(modelOverride.base_url).name
    : (status.data?.provider ?? '');
  const modelLabel = pillLabel(pillProvider, pillModel);

  const mention: MentionQuery | null =
    !dismissed && !menuOpen && !modelOpen && resources.length > 0
      ? activeMention(value, caret)
      : null;
  const suggestions = React.useMemo(
    () => (mention ? filterMentions(resources, mention.query) : []),
    [mention, resources],
  );

  const speech = useSpeechInput(
    (transcript) => {
      onChange(`${voiceBase.current} ${transcript}`.replace(/\s+/g, ' ').trimStart());
    },
    {
      onStart: () => onVoiceActivity?.({ listening: true, level: 0 }),
      onLevel: (level) => onVoiceActivity?.({ listening: true, level }),
      onStop: () => onVoiceActivity?.({ listening: false, level: 0 }),
      onError: (message) =>
        onVoiceActivity?.({ listening: false, level: 0, error: message }),
    },
  );

  // Grow with the content.
  React.useEffect(() => {
    const node = inputRef.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, large ? 220 : 176)}px`;
  }, [value, large]);

  React.useEffect(() => {
    if (!autoFocus) return;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 40);
    return () => window.clearTimeout(timer);
  }, [autoFocus]);

  // Restore the caret after inserting a mention or starter.
  React.useEffect(() => {
    if (pendingCaret.current === null) return;
    const node = inputRef.current;
    const position = pendingCaret.current;
    pendingCaret.current = null;
    if (!node) return;
    node.focus();
    node.setSelectionRange(position, position);
    setCaret(position);
  }, [value]);

  const resetMention = () => {
    setDismissed(true);
    setMenuOpen(false);
    setModelOpen(false);
  };

  const choose = (resource: MentionResource) => {
    if (!mention) return;
    const next = applyMention(value, mention, resource);
    pendingCaret.current = next.caret;
    onChange(next.text);
    setDismissed(true);
    setHighlight(0);
  };

  const injectMention = () => {
    if (disabled) return;
    const node = inputRef.current;
    const start = node?.selectionStart ?? value.length;
    const next = `${value.slice(0, start)}@${value.slice(start)}`;
    pendingCaret.current = start + 1;
    onChange(next);
    setDismissed(false);
    setMenuOpen(false);
    setModelOpen(false);
  };

  const insertStarter = (starter: string) => {
    onChange(value.trim() ? `${value.trim()} ${starter}` : starter);
    setMenuOpen(false);
    pendingCaret.current = null;
    inputRef.current?.focus();
  };

  const pickFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const result = await readAttachments(Array.from(files), attachments);
    if (result.accepted.length > 0) {
      setAttachments((current) => [...current, ...result.accepted]);
      setFileNote(null);
    }
    if (result.skipped.length > 0) {
      setFileNote(result.skipped.map((item) => item.reason).join(' '));
    }
    if (fileRef.current) fileRef.current.value = '';
  };

  const submit = () => {
    if (!sendable) return;
    onSend({ text: trimmed, attachments });
    setAttachments([]);
    setFileNote(null);
    setDismissed(true);
    speech.stop();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention && suggestions.length > 0) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setHighlight((index) => (index + 1) % suggestions.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setHighlight((index) => (index - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        choose(suggestions[Math.min(highlight, suggestions.length - 1)]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        resetMention();
        return;
      }
    }
    if (event.key === 'Escape' && (menuOpen || modelOpen)) {
      event.preventDefault();
      resetMention();
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className={cn('relative', className)}>
      {mention && suggestions.length > 0 && (
        <div
          className="absolute bottom-full left-0 z-40 mb-2 w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-popover p-1 shadow-2xl"
          role="listbox"
          aria-label="Mention suggestions"
        >
          {suggestions.map((resource, index) => (
            <button
              key={`${resource.kind}:${resource.id}`}
              role="option"
              aria-selected={index === highlight}
              onMouseEnter={() => setHighlight(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                choose(resource);
              }}
              className={cn(
                'flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left',
                index === highlight ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
              )}
            >
              <span className="mt-0.5 rounded border border-border/70 px-1 py-0.5 text-[9px] uppercase tracking-wide text-muted-foreground">
                {resource.kind}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium">{resource.label}</span>
                {resource.detail && (
                  <span className="block truncate text-[10px] text-muted-foreground">
                    {resource.detail}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}

      {modelOpen && (
        <ModelPicker
          onClose={() => setModelOpen(false)}
          override={modelOverride ?? null}
          onOverride={onModelOverride}
        />
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className={cn(
          'rounded-2xl border border-border bg-card p-2 shadow-sm transition-colors focus-within:border-primary/60',
          disabled && 'opacity-70',
        )}
      >
        {attachments.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            {attachments.map((attachment) => (
              <span
                key={attachment.id}
                className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border/70 bg-muted/40 px-2 py-0.5 text-[11px]"
              >
                <FileText className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                <span className="truncate">{attachment.name}</span>
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {formatBytes(attachment.size)}
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${attachment.name}`}
                  onClick={() =>
                    setAttachments((current) =>
                      current.filter((item) => item.id !== attachment.id),
                    )
                  }
                  className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={inputRef}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
            setCaret(event.target.selectionStart ?? event.target.value.length);
            setDismissed(false);
            setHighlight(0);
          }}
          onKeyDown={onKeyDown}
          onKeyUp={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
          onClick={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
          onBlur={() => setCaret(0)}
          rows={large ? 3 : 1}
          disabled={disabled}
          placeholder={placeholder}
          aria-label="Message the assistant"
          className={cn(
            'w-full resize-none bg-transparent px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed',
            large ? 'min-h-[92px]' : 'min-h-[52px]',
          )}
        />

        {fileNote && (
          <p className="mb-1 flex items-start gap-1.5 rounded-md bg-amber-500/[0.08] px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300">
            {fileNote}
          </p>
        )}

        <div className="flex items-center gap-1 px-1 pb-0.5">
          <div className="relative">
            <button
              type="button"
              aria-label="Add attachment, starter or mode"
              aria-expanded={menuOpen}
              disabled={disabled}
              onClick={() => {
                setMenuOpen((open) => !open);
                setModelOpen(false);
                setDismissed(true);
              }}
              className={cn(
                'inline-flex h-7 w-7 items-center justify-center rounded-full border border-border/70 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                menuOpen && 'bg-accent text-foreground',
              )}
            >
              <Plus className={cn('h-4 w-4 transition-transform', menuOpen && 'rotate-45')} />
            </button>

            {menuOpen && (
              <div className="absolute bottom-full left-0 z-40 mb-2 w-[min(320px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-popover p-1 shadow-2xl">
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-accent"
                >
                  <Paperclip className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1">
                    Attach files
                    <span className="block text-[10px] text-muted-foreground">
                      Text, CSV, JSON, YAML, logs · up to {formatBytes(MAX_ATTACHMENT_BYTES)}
                    </span>
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    if (onModeChange) onModeChange(mode === 'build' ? 'plan' : 'build');
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-accent"
                >
                  {mode === 'build' ? (
                    <Hammer className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  ) : (
                    <Map className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  )}
                  <span className="min-w-0 flex-1">
                    {mode === 'build' ? 'Build mode' : 'Plan mode'}
                    <span className="block text-[10px] text-muted-foreground">
                      Switch to {mode === 'build' ? 'plan first, change nothing' : 'apply changes'}
                    </span>
                  </span>
                </button>

                {starters.length > 0 && (
                  <>
                    <p className="px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                      Starters
                    </p>
                    {starters.map((starter) => (
                      <button
                        key={starter}
                        type="button"
                        onClick={() => insertStarter(starter)}
                        className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        <Sparkles className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                        {starter}
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>

          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(event) => void pickFiles(event.target.files)}
            accept=".txt,.md,.csv,.tsv,.json,.yaml,.yml,.xml,.log,.ini,.env,text/*,application/json"
          />

          {resources.length > 0 && (
            <button
              type="button"
              onClick={injectMention}
              disabled={disabled}
              aria-label="Mention a workflow or snippet"
              title="Mention a workflow, node or saved prompt"
              className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <AtSign className="h-4 w-4" />
            </button>
          )}

          <button
            type="button"
            onClick={() => {
              voiceBase.current = value;
              speech.toggle();
            }}
            disabled={disabled || !speech.supported}
            aria-pressed={speech.listening}
            aria-label={speech.listening ? 'Stop dictation' : 'Dictate a message'}
            title={
              speech.supported
                ? speech.listening
                  ? 'Listening — click to stop'
                  : 'Dictate a message'
                : 'Dictation needs Chrome, Edge or Safari'
            }
            className={cn(
              'inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40',
              speech.listening && 'bg-primary/10 text-primary oa-pulse-ring',
            )}
          >
            {speech.listening ? (
              <Square className="h-3.5 w-3.5" />
            ) : (
              <Mic className="h-4 w-4" />
            )}
          </button>

          {onModeChange && (
            <button
              type="button"
              onClick={() => onModeChange(mode === 'build' ? 'plan' : 'build')}
              disabled={disabled}
              aria-label={mode === 'build' ? 'Switch to plan mode' : 'Switch to build mode'}
              title={
                mode === 'build'
                  ? 'Build mode — creates and edits workflows'
                  : 'Plan mode — answers first, changes nothing'
              }
              className="ml-0.5 inline-flex items-center gap-1 rounded-full border border-border/70 px-2 py-1 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {mode === 'build' ? (
                <Hammer className="h-3 w-3" aria-hidden />
              ) : (
                <Map className="h-3 w-3" aria-hidden />
              )}
              {mode === 'build' ? 'Build' : 'Plan'}
            </button>
          )}

          <div className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                setModelOpen((open) => !open);
                setMenuOpen(false);
                setDismissed(true);
              }}
              aria-expanded={modelOpen}
              aria-label={`Choose model. Current: ${modelLabel}`}
              title="Local models and API keys"
              className={cn(
                'inline-flex max-w-[190px] items-center gap-1.5 rounded-full border border-border/70 px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                modelOpen && 'bg-accent text-foreground',
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 shrink-0 rounded-full',
                  status.data?.ready ? 'bg-emerald-500' : 'bg-amber-500',
                )}
                aria-hidden
              />
              <span className="truncate">{modelLabel}</span>
              {modelOverride && (
                <span className="shrink-0 rounded-full bg-primary/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary">
                  chat
                </span>
              )}
            </button>
            {modelOverride && onModelOverride && (
              <button
                type="button"
                onClick={() => onModelOverride(null)}
                aria-label="Use the default model for this chat"
                title="Back to the default model"
                className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            )}
            <Button
              type={busy && onStop ? 'button' : 'submit'}
              size="icon"
              disabled={busy ? !onStop : !sendable}
              onClick={busy && onStop ? () => onStop() : undefined}
              aria-label={busy ? 'Stop the assistant' : 'Send message'}
              title={busy ? (onStop ? 'Stop' : 'Waiting for the assistant') : 'Send (Enter)'}
              className="h-9 w-9 shrink-0 rounded-full"
            >
              {busy ? (
                onStop ? (
                  <Square className="h-3 w-3" fill="currentColor" aria-hidden />
                ) : (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                )
              ) : (
                <ArrowUp className="h-4 w-4" aria-hidden />
              )}
            </Button>
          </div>
        </div>

        {(speech.error || speech.listening) && (
          <p className="px-2 pb-0.5 pt-1 text-[10px] text-muted-foreground">
            {speech.listening ? 'Listening… speak now.' : speech.error}
          </p>
        )}
      </form>

      {footer}
    </div>
  );
}
