'use client';

// One chat surface for both places a workflow is talked to: the studio's Chat
// tab (compact) and the hosted /chat/[workflowId] page (full). Sessions and
// transcripts live in localStorage; each turn runs the real workflow through
// the Chat Trigger and streams the model reply.

import * as React from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  ListOrdered,
  Loader2,
  MessageSquarePlus,
  PanelLeft,
  Paperclip,
  Pencil,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import { toUserMessage } from '@/lib/api';
import { useOrganization } from '@/context/OrganizationContext';
import { cn } from '@/lib/utils';
import type { WorkflowDefinition } from '@/features/workflows/types';
import {
  activeSession,
  appendMessage,
  clearSessionMessages,
  createSession,
  deleteSession,
  emptyChatState,
  ensureSession,
  loadChatState,
  newMessageId,
  renameSession,
  saveChatState,
  selectSession,
  sessionTitleFrom,
  transcriptFor,
  updateMessage,
} from '@/features/chat/sessions';
import { streamWorkflowChat, type ChatTurn } from '@/features/chat/chat-api';
import { emptyReplyHint, formatBytes, formatDuration, runFromDone } from '@/features/chat/format';
import { MAX_ATTACHMENTS, readAttachment, validateAttachment } from '@/features/chat/files';
import type {
  ChatAttachment,
  ChatMessage,
  ChatRunMeta,
  ChatRunStep,
  ChatState,
} from '@/features/chat/types';
import { Markdown } from './Markdown';

export interface OpenAgentChatSystemProps {
  workflowId: string;
  workflowName?: string;
  /** The studio's unsaved canvas; omitted on the hosted page (latest version). */
  definition?: WorkflowDefinition;
  variant?: 'panel' | 'full';
  title?: string;
  welcome?: string;
  starters?: string[];
  /** Disable sending (read-only workflow). */
  disabled?: boolean;
  /** Hide the session rail initially in the compact panel. */
  onInspectNode?: (nodeId: string, executionId: string | null) => void;
  onClose?: () => void;
  className?: string;
}

function statusDot(status: string): string {
  if (status === 'succeeded' || status === 'completed') return 'bg-emerald-500';
  if (status === 'failed') return 'bg-destructive';
  if (status === 'waiting') return 'bg-amber-500';
  return 'bg-muted-foreground/50';
}

function MessageSteps({
  run,
  onInspect,
}: {
  run: ChatRunMeta;
  onInspect?: (nodeId: string, executionId: string | null) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const failed = run.steps.filter((step) => step.status === 'failed').length;
  const target = run.replyNodeId ?? run.steps[run.steps.length - 1]?.node_id ?? null;

  return (
    <div className="mt-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="inline-flex items-center gap-1 rounded-full border border-border/70 bg-card px-2 py-0.5 text-[10.5px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ListOrdered className="h-3 w-3" aria-hidden />
          {run.steps.length} step{run.steps.length === 1 ? '' : 's'}
          <span aria-hidden>·</span>
          {formatDuration(run.duration_ms)}
          {failed > 0 && <span className="font-semibold text-destructive">· {failed} failed</span>}
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </button>
        {onInspect && target && (
          <button
            type="button"
            onClick={() => onInspect(target, run.execution_id)}
            className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10.5px] font-medium text-primary hover:bg-primary/10"
          >
            Inspect in canvas
            <ExternalLink className="h-3 w-3" aria-hidden />
          </button>
        )}
        {run.model && (
          <span className="truncate text-[10.5px] text-muted-foreground" title={run.model}>
            {run.model}
          </span>
        )}
      </div>

      {open && (
        <ol className="mt-1.5 space-y-1 rounded-lg border border-border/70 bg-muted/30 p-2">
          {run.steps.map((step: ChatRunStep) => (
            <li key={step.node_id} className="flex items-start gap-2 text-[11px]">
              <span className={cn('mt-1 h-1.5 w-1.5 shrink-0 rounded-full', statusDot(step.status))} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium text-foreground">{step.node_name || step.node_id}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {formatDuration(step.duration_ms)}
                  </span>
                </div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {step.node_type || 'node'}
                  {step.status !== 'succeeded' ? ` · ${step.status}` : ''}
                  {step.reason ? ` · ${step.reason}` : ''}
                </div>
                {step.error && <p className="mt-0.5 break-words text-destructive">{step.error}</p>}
              </div>
              {onInspect && (
                <button
                  type="button"
                  onClick={() => onInspect(step.node_id, run.execution_id)}
                  aria-label={`Inspect ${step.node_name || step.node_id} in canvas`}
                  className="oa-icon-button h-5 w-5 shrink-0"
                >
                  <ExternalLink className="h-3 w-3" />
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AttachmentChips({
  files,
  onRemove,
}: {
  files: ChatAttachment[];
  onRemove?: (index: number) => void;
}) {
  if (files.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {files.map((file, index) => (
        <span
          key={`${file.name}-${index}`}
          className="inline-flex max-w-[220px] items-center gap-1 rounded-md border border-border/70 bg-card px-1.5 py-0.5 text-[11px]"
        >
          <Paperclip className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{file.name}</span>
          <span className="shrink-0 text-muted-foreground">{formatBytes(file.size)}</span>
          {onRemove && (
            <button
              type="button"
              onClick={() => onRemove(index)}
              aria-label={`Remove ${file.name}`}
              className="oa-icon-button h-4 w-4"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

function MessageBubble({
  message,
  onInspect,
  onRetry,
}: {
  message: ChatMessage;
  onInspect?: (nodeId: string, executionId: string | null) => void;
  onRetry?: () => void;
}) {
  const isUser = message.role === 'user';
  return (
    <div className={cn('flex gap-2', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Bot className="h-3.5 w-3.5" aria-hidden />
        </span>
      )}
      <div className={cn('min-w-0 max-w-[88%]', isUser && 'flex flex-col items-end')}>
        <div
          className={cn(
            'rounded-2xl px-3 py-2',
            isUser
              ? 'rounded-br-sm bg-primary text-primary-foreground'
              : 'rounded-bl-sm border border-border/70 bg-card text-foreground',
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed">{message.content}</p>
          ) : (
            <>
              {message.content ? (
                <Markdown content={message.content} />
              ) : message.pending ? (
                <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Waiting for the workflow…
                </span>
              ) : null}
              {message.pending && message.content && (
                <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-primary align-middle" aria-hidden />
              )}
            </>
          )}
        </div>

        {message.attachments && message.attachments.length > 0 && (
          <div className="mt-1 max-w-full">
            <AttachmentChips files={message.attachments} />
          </div>
        )}

        {message.error && (
          <p className="mt-1 flex items-start gap-1 text-[11px] text-destructive">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
            <span className="break-words">{message.error}</span>
          </p>
        )}

        {message.run && <MessageSteps run={message.run} onInspect={onInspect} />}

        {!isUser && !message.pending && (message.error || message.run?.status === 'failed') && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-1 text-[11px] font-medium text-primary hover:underline"
          >
            Retry this turn
          </button>
        )}
        <span className="mt-1 text-[10px] text-muted-foreground">
          {new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    </div>
  );
}

export function OpenAgentChatSystem({
  workflowId,
  workflowName,
  definition,
  variant = 'panel',
  title,
  welcome,
  starters,
  disabled = false,
  onInspectNode,
  onClose,
  className,
}: OpenAgentChatSystemProps) {
  const { currentOrgId } = useOrganization();
  const [state, setState] = React.useState<ChatState>(() => loadChatState(workflowId));
  const [draft, setDraft] = React.useState('');
  const [attachments, setAttachments] = React.useState<ChatAttachment[]>([]);
  const [streaming, setStreaming] = React.useState(false);
  const [attachmentError, setAttachmentError] = React.useState<string | null>(null);
  const [modelLabel, setModelLabel] = React.useState('');
  const [showSessions, setShowSessions] = React.useState(variant === 'full');
  const [renaming, setRenaming] = React.useState<string | null>(null);
  const [renameDraft, setRenameDraft] = React.useState('');

  const session = activeSession(state);
  const messages = session?.messages ?? [];
  const abortRef = React.useRef<AbortController | null>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);

  // A different workflow means a different transcript.
  React.useEffect(() => {
    setState(loadChatState(workflowId));
    setDraft('');
    setAttachments([]);
    setAttachmentError(null);
  }, [workflowId]);

  React.useEffect(() => {
    saveChatState(state);
  }, [state]);

  React.useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, streaming, session?.id]);

  React.useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const run = async (
    sessionId: string,
    text: string,
    history: ChatTurn[],
    files: ChatAttachment[],
    assistantId: string,
  ) => {
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    let accumulated = '';

    try {
      const done = await streamWorkflowChat(
        {
          orgId: currentOrgId,
          workflowId,
          message: text,
          sessionId,
          history,
          files: files.length > 0 ? files : undefined,
          definition,
        },
        {
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === 'meta') {
              setModelLabel(event.data.model || '');
            }
            if (event.type === 'token') {
              accumulated += event.data.text;
              const snapshot = accumulated;
              setState((prev) =>
                updateMessage(prev, sessionId, assistantId, { content: snapshot, pending: true }),
              );
            }
          },
        },
      );

      const content = (done.reply || accumulated).trim();
      setState((prev) =>
        updateMessage(prev, sessionId, assistantId, {
          content: content || emptyReplyHint(done),
          // No-reply hints are guidance; only a failed run is an error.
          error: done.status === 'failed' ? done.error || 'The workflow run failed.' : null,
          pending: false,
          run: runFromDone(done),
        }),
      );
    } catch (error) {
      if (controller.signal.aborted) {
        setState((prev) =>
          updateMessage(prev, sessionId, assistantId, {
            content: accumulated || 'Stopped.',
            pending: false,
          }),
        );
      } else {
        setState((prev) =>
          updateMessage(prev, sessionId, assistantId, {
            content: accumulated,
            error: toUserMessage(error),
            pending: false,
          }),
        );
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
    }
  };

  const sendTurn = (rawText: string, files: ChatAttachment[]) => {
    const text = rawText.trim();
    if (!text || streaming || disabled) return;

    const ensured = ensureSession(state);
    let next = ensured.state;
    const sessionId = ensured.sessionId;
    const isNew = !activeSession(state);
    if (isNew) next = renameSession(next, sessionId, sessionTitleFrom(text));

    const history = transcriptFor(activeSession(next));
    const assistantId = newMessageId();
    const now = Date.now();
    next = appendMessage(next, sessionId, {
      id: newMessageId(),
      role: 'user',
      content: text,
      createdAt: now,
      attachments: files.length > 0 ? files : undefined,
    });
    next = appendMessage(next, sessionId, {
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: now + 1,
      pending: true,
    });

    setState(next);
    setDraft('');
    setAttachments([]);
    setAttachmentError(null);
    void run(sessionId, text, history, files, assistantId);
  };

  const retryTurn = (sessionId: string) => {
    if (streaming) return;
    const target = state.sessions.find((s) => s.id === sessionId);
    if (!target) return;
    const lastUser = [...target.messages].reverse().find((m) => m.role === 'user');
    if (!lastUser) return;
    const history = transcriptFor(
      { ...target, messages: target.messages.slice(0, target.messages.indexOf(lastUser)) },
    );
    const assistantId = newMessageId();
    const appended = appendMessage(state, sessionId, {
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
      pending: true,
    });
    setState(appended);
    void run(sessionId, lastUser.content, history, lastUser.attachments ?? [], assistantId);
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  };

  const attachFiles = async (files: FileList | File[]) => {
    setAttachmentError(null);
    const incoming = Array.from(files);
    const accepted: ChatAttachment[] = [];
    for (const file of incoming) {
      if (attachments.length + accepted.length >= MAX_ATTACHMENTS) {
        setAttachmentError(`Attach at most ${MAX_ATTACHMENTS} files per message.`);
        break;
      }
      const problem = validateAttachment(file);
      if (problem) {
        setAttachmentError(problem);
        continue;
      }
      try {
        accepted.push(await readAttachment(file));
      } catch (error) {
        setAttachmentError(toUserMessage(error));
      }
    }
    if (accepted.length > 0) {
      setAttachments((prev) => [...prev, ...accepted].slice(0, MAX_ATTACHMENTS));
    }
  };

  const startNewChat = () => {
    if (streaming) return;
    setState(createSession(state));
    setShowSessions(variant === 'full');
    inputRef.current?.focus();
  };

  const activeTitle = title ?? workflowName ?? 'Workflow chat';
  const welcomeText =
    welcome ??
    'Talk to this workflow. Every message runs the live graph from its Chat Trigger — no cloud service involved.';
  const starterPrompts = (starters ?? []).filter(Boolean);
  const compact = variant === 'panel';

  return (
    <div className={cn('relative flex h-full min-h-0 w-full overflow-hidden bg-background', className)}>
      {showSessions && (
        <>
          {compact && (
            <button
              type="button"
              aria-label="Close chat list"
              onClick={() => setShowSessions(false)}
              className="absolute inset-0 z-10 bg-background/60 backdrop-blur-[1px]"
            />
          )}
          <aside
            aria-label="Chat sessions"
            className={cn(
              'flex min-h-0 flex-col border-r border-border bg-card/60',
              compact ? 'absolute inset-y-0 left-0 z-20 w-56 shadow-lg' : 'w-[232px] shrink-0',
            )}
          >
            <div className="flex items-center gap-1 border-b border-border/70 px-2 py-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                Chats
              </span>
              <button
                type="button"
                onClick={startNewChat}
                className="oa-icon-button ml-auto h-6 w-6"
                aria-label="New chat"
                title="New chat"
              >
                <MessageSquarePlus className="h-3.5 w-3.5" />
              </button>
              {!compact && (
                <button
                  type="button"
                  onClick={() => setShowSessions(false)}
                  className="oa-icon-button h-6 w-6"
                  aria-label="Close chat list"
                >
                  <PanelLeft className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-1">
              {state.sessions.length === 0 && (
                <p className="px-2 py-3 text-[11px] text-muted-foreground">No conversations yet.</p>
              )}
              {state.sessions.map((item) => (
                <div
                  key={item.id}
                  className={cn(
                    'group flex items-center gap-1 rounded-md px-1.5 py-1 text-[12px]',
                    item.id === state.activeId ? 'bg-primary/10 text-foreground' : 'hover:bg-muted/60',
                  )}
                >
                  {renaming === item.id ? (
                    <input
                      autoFocus
                      value={renameDraft}
                      onChange={(event) => setRenameDraft(event.target.value)}
                      onBlur={() => {
                        setState(renameSession(state, item.id, renameDraft));
                        setRenaming(null);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          setState(renameSession(state, item.id, renameDraft));
                          setRenaming(null);
                        }
                        if (event.key === 'Escape') setRenaming(null);
                      }}
                      aria-label="Chat title"
                      className="min-w-0 flex-1 rounded border border-border bg-background px-1 py-0.5 text-[12px]"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setState(selectSession(state, item.id));
                        if (compact) setShowSessions(false);
                      }}
                      onDoubleClick={() => {
                        setRenameDraft(item.title);
                        setRenaming(item.id);
                      }}
                      className="min-w-0 flex-1 truncate text-left"
                      title={item.title}
                    >
                      {item.title}
                      <span className="ml-1 text-[10px] text-muted-foreground">
                        {item.messages.length}
                      </span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setRenameDraft(item.title);
                      setRenaming(item.id);
                    }}
                    aria-label={`Rename ${item.title}`}
                    className="oa-icon-button h-5 w-5 opacity-0 group-hover:opacity-100 focus:opacity-100"
                  >
                    <Pencil className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setState(deleteSession(state, item.id))}
                    aria-label={`Delete ${item.title}`}
                    className="oa-icon-button h-5 w-5 opacity-0 group-hover:opacity-100 focus:opacity-100"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
            {session && (
              <button
                type="button"
                onClick={() => setState(clearSessionMessages(state, session.id))}
                className="border-t border-border/70 px-2 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              >
                Clear this transcript
              </button>
            )}
          </aside>
        </>
      )}

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-2">
          {!showSessions && (
            <button
              type="button"
              onClick={() => setShowSessions(true)}
              aria-label="Show chat list"
              className="oa-icon-button h-6 w-6"
            >
              <PanelLeft className="h-3.5 w-3.5" />
            </button>
          )}
          <div className="min-w-0">
            <p className="truncate text-[12.5px] font-semibold leading-tight">{activeTitle}</p>
            <p className="truncate text-[10px] leading-tight text-muted-foreground">
              {modelLabel ? `Local model · ${modelLabel}` : 'Runs this workflow'}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-0.5">
            {!compact && (
              <Link
                href={`/workflows/${workflowId}/edit`}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                Open in editor
                <ExternalLink className="h-3 w-3" aria-hidden />
              </Link>
            )}
            {compact && (
              <Link
                href={`/chat/${workflowId}`}
                target="_blank"
                aria-label="Open hosted chat page"
                title="Open hosted chat page"
                className="oa-icon-button h-6 w-6"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </Link>
            )}
            <button
              type="button"
              onClick={startNewChat}
              aria-label="New chat"
              title="New chat"
              className="oa-icon-button h-6 w-6"
            >
              <MessageSquarePlus className="h-3.5 w-3.5" />
            </button>
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close chat"
                className="oa-icon-button h-6 w-6"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </header>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
          onDragOver={(event) => {
            if (event.dataTransfer.types.includes('Files')) event.preventDefault();
          }}
          onDrop={(event) => {
            if (event.dataTransfer.files.length > 0) {
              event.preventDefault();
              void attachFiles(event.dataTransfer.files);
            }
          }}
        >
          {messages.length === 0 && (
            <div className="rounded-xl border border-dashed border-border/80 bg-muted/20 p-3">
              <p className="text-[13px] font-semibold">{activeTitle}</p>
              <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{welcomeText}</p>
              {starterPrompts.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {starterPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => sendTurn(prompt, [])}
                      disabled={disabled || streaming}
                      className="rounded-full border border-border bg-card px-2.5 py-1 text-left text-[11px] hover:border-primary/50 hover:text-primary disabled:opacity-50"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              onInspect={onInspectNode}
              onRetry={
                session && message.role === 'assistant'
                  ? () => retryTurn(session.id)
                  : undefined
              }
            />
          ))}
        </div>

        <div className="shrink-0 border-t border-border p-2">
          {attachments.length > 0 && (
            <div className="mb-1.5">
              <AttachmentChips
                files={attachments}
                onRemove={(index) =>
                  setAttachments((prev) => prev.filter((_, i) => i !== index))
                }
              />
            </div>
          )}
          {attachmentError && (
            <p className="mb-1 text-[11px] text-destructive">{attachmentError}</p>
          )}
          <div className="flex items-end gap-1 rounded-xl border border-border bg-card p-1 focus-within:border-primary/60">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              aria-label="Attach files"
              title="Attach text, CSV, JSON, PDF or image"
              disabled={disabled}
              className="oa-icon-button h-7 w-7 shrink-0"
            >
              <Paperclip className="h-3.5 w-3.5" />
            </button>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              accept="text/*,application/json,application/pdf,image/*"
              onChange={(event) => {
                if (event.target.files) void attachFiles(event.target.files);
                event.target.value = '';
              }}
            />
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  sendTurn(draft, attachments);
                }
              }}
              rows={1}
              placeholder={disabled ? 'This workflow is read-only' : 'Message this workflow…'}
              disabled={disabled}
              aria-label="Chat message"
              className="max-h-32 min-h-[26px] flex-1 resize-none bg-transparent px-1 py-1 text-[12.5px] leading-snug outline-none placeholder:text-muted-foreground disabled:opacity-60"
            />
            {streaming ? (
              <button
                type="button"
                onClick={stop}
                aria-label="Stop reply"
                title="Stop reply"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground hover:bg-muted/80"
              >
                <Square className="h-3 w-3" />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => sendTurn(draft, attachments)}
                disabled={disabled || !draft.trim()}
                aria-label="Send message"
                title="Send (Enter)"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40"
              >
                {streaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-3.5 w-3.5" />}
              </button>
            )}
          </div>
          <p className="mt-1 flex items-center gap-1 px-1 text-[10px] text-muted-foreground">
            <Check className="h-2.5 w-2.5" aria-hidden />
            Transcripts stay in this browser · Enter sends, Shift+Enter adds a line
          </p>
        </div>
      </section>
    </div>
  );
}
