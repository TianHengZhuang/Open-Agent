'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import {
  Bot,
  Check,
  Cpu,
  Download,
  KeyRound,
  Loader2,
  MessagesSquare,
  PanelLeft,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Radio,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Workflow,
  X,
} from 'lucide-react';
import {
  AssistantStepsCard,
  type DisplayAction,
} from '@/components/assistant/AssistantActionCard';
import {
  AssistantComposer,
  type ComposerMode,
} from '@/components/assistant/AssistantComposer';
import { Markdown } from '@/components/chat/Markdown';
import { Button } from '@/components/ui/button';
import { toUserMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useLocalStorage } from '@/hooks/hooks';
import { useOrganization } from '@/context/OrganizationContext';
import { streamKoroChat } from '@/features/koro/chat-stream';
import {
  useAssistantConversation,
  useAssistantConversations,
  useDeleteAssistantConversation,
  useKoroStatus,
  useRenameAssistantConversation,
} from '@/features/koro/koro-api';
import { useChatModelOverride } from '@/features/koro/use-chat-model';
import { buildKoroContext } from '@/features/koro/context';
import { useWorkflows } from '@/features/workflows/workflows-api';
import { withAttachments } from '@/features/assistant/attachments';
import { useCredentials } from '@/features/integrations/integrations-api';
import { RobotCompanion } from '@/components/robot/RobotCompanion';
import { useRobotCompanion } from '@/features/robot/use-robot-companion';
import {
  buildMentionResources,
  loadSavedPrompts,
} from '@/features/assistant/resources';
import {
  downloadTextFile,
  exportFilename,
  filterChats,
  groupChats,
  sortChats,
  togglePin,
  transcriptMarkdown,
} from '@/features/assistant/chat-list';
import type { MentionResource } from '@/features/assistant/mentions';

/** One rendered line of the conversation (live or replayed from history). */
type Entry = {
  role: 'user' | 'assistant';
  content: string;
  source?: string;
  actions?: DisplayAction[];
};

const QUICK_PROMPTS = [
  'Build a workflow that triages support emails',
  'Show the versions of my main workflow',
  'What is running right now?',
  'List my credentials',
];

const STARTERS = [
  'Build a webhook to AI + Slack workflow',
  'Duplicate my main workflow and publish it',
  'Debug my last failed run',
  'Review my open workflow and suggest fixes',
];

const NAV_LINKS = [
  { href: '/workflows', label: 'Workflows', icon: Workflow },
  { href: '/runs', label: 'Executions', icon: Radio },
  { href: '/settings?tab=credentials', label: 'Credentials', icon: KeyRound },
  { href: '/settings/models', label: 'Models & keys', icon: Cpu },
  { href: '/settings', label: 'Settings', icon: Settings },
];

function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function AssistantWorkspace() {
  const queryClient = useQueryClient();
  const conversations = useAssistantConversations();
  const [activeId, setActiveId] = React.useState<string | null>(null);
  // Bumped only when the user switches chats, so sending the first message
  // (which creates a transcript id) does not drop the chosen model.
  const [chatEpoch, setChatEpoch] = React.useState(0);
  const { override, setOverride } = useChatModelOverride(String(chatEpoch));
  const detail = useAssistantConversation(activeId);
  const { currentOrgId } = useOrganization();
  const [busy, setBusy] = React.useState(false);
  const status = useKoroStatus(true);
  const rename = useRenameAssistantConversation();
  const remove = useDeleteAssistantConversation();
  const workflows = useWorkflows({ pageSize: 50 });
  const credentials = useCredentials();
  const abortRef = React.useRef<AbortController | null>(null);
  const robot = useRobotCompanion();

  const [entries, setEntries] = React.useState<Entry[]>([]);
  const [draft, setDraft] = React.useState('');
  const [mode, setMode] = React.useState<ComposerMode>('build');
  const [error, setError] = React.useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = React.useState(true);
  const [robotOpen, setRobotOpen] = useLocalStorage<boolean>('oa:assistant-robot-open', true);
  const [mobileRobotOpen, setMobileRobotOpen] = React.useState(false);
  const [mobileHistoryOpen, setMobileHistoryOpen] = React.useState(false);
  const [renaming, setRenaming] = React.useState<{ id: string; title: string } | null>(null);
  const [search, setSearch] = React.useState('');
  const [pinned, setPinned] = useLocalStorage<string[]>('oa:assistant-pinned', []);
  const scrollRef = React.useRef<HTMLDivElement>(null);

  // Replay a saved transcript into the thread.
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
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [entries, busy]);

  const startNewChat = () => {
    robot.stopSpeaking();
    robot.clearReply();
    robot.reset();
    setActiveId(null);
    setEntries([]);
    setDraft('');
    setError(null);
    setChatEpoch((epoch) => epoch + 1);
    setMobileHistoryOpen(false);
  };

  const openConversation = (id: string) => {
    if (id === activeId) {
      setMobileHistoryOpen(false);
      return;
    }
    robot.stopSpeaking();
    robot.clearReply();
    robot.reset();
    setActiveId(id);
    setEntries([]);
    setError(null);
    setChatEpoch((epoch) => epoch + 1);
    setMobileHistoryOpen(false);
  };

  const send = async (payload: { text: string; attachments: Parameters<typeof withAttachments>[1] }) => {
    const message = payload.text.trim();
    if (!message || busy || !currentOrgId) return;
    const outgoing = withAttachments(message, payload.attachments);
    const history = entries.slice(-6).map((entry) => ({ role: entry.role, content: entry.content }));

    setEntries((prev) => [
      ...prev,
      { role: 'user', content: message },
      { role: 'assistant', content: '' },
    ]);
    setDraft('');
    setError(null);
    setBusy(true);
    robot.requestStarted();
    let streamed = false;
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
              null,
              typeof window !== 'undefined' ? window.location.pathname : undefined,
            ),
            mode,
          },
        },
        {
          signal: controller.signal,
          onDelta: (text) => {
            if (!streamed) {
              streamed = true;
              robot.responseStreaming();
            }
            setEntries((prev) => {
              const next = [...prev];
              const last = next.length - 1;
              if (last < 0 || next[last].role !== 'assistant') return prev;
              next[last] = { ...next[last], content: next[last].content + text };
              return next;
            });
          },
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
      robot.completed(result.reply, true);
      if (!activeId && result.conversation_id) {
        // The transcript now exists server-side; the detail query replays it
        // (replacing the optimistic thread with the saved messages).
        setActiveId(result.conversation_id);
      }
      void queryClient.invalidateQueries({ queryKey: ['koro', 'conversations'] });
      void queryClient.invalidateQueries({ queryKey: ['koro', 'conversation'] });
      // Copilot actions can create, run or publish workflows and agents.
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
      void queryClient.invalidateQueries({ queryKey: ['agents'] });
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') {
        robot.reset();
        return;
      }
      const text = toUserMessage(e);
      setError(text);
      robot.failed();
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
  };

  const submitRename = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!renaming || !renaming.title.trim()) return;
    try {
      await rename.mutateAsync({ id: renaming.id, title: renaming.title.trim() });
      setRenaming(null);
    } catch (e) {
      setError(toUserMessage(e));
    }
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

  const exportConversation = () => {
    const title = activeTitle ?? 'Assistant chat';
    const messages =
      detail.data?.id === activeId && detail.data.messages.length > 0
        ? detail.data.messages
        : entries;
    downloadTextFile(exportFilename(title), transcriptMarkdown(title, messages));
  };

  const items = conversations.data ?? [];
  const visible = sortChats(filterChats(items, search), pinned);
  const grouped = groupChats(visible);
  const activeTitle =
    detail.data?.id === activeId
      ? detail.data?.title
      : items.find((item) => item.id === activeId)?.title;

  const ready = status.data?.ready;
  const local = status.data?.local ?? true;

  const resources = React.useMemo<MentionResource[]>(() => {
    return buildMentionResources({
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
      prompts: STARTERS,
      savedPrompts: loadSavedPrompts(),
    });
  }, [workflows.items, credentials.credentials]);

  const historyList = (
    <div className="oa-scroll flex-1 overflow-y-auto p-2">
      {conversations.isLoading ? (
        <p className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading chats…
        </p>
      ) : conversations.isError ? (
        <div className="px-2 py-3">
          <p className="text-xs text-destructive">{toUserMessage(conversations.error)}</p>
          <Button
            size="sm"
            variant="outline"
            className="mt-2 h-7 text-[11px]"
            onClick={() => void conversations.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : grouped.length === 0 ? (
        <p className="px-2 py-3 text-xs text-muted-foreground">
          {search.trim() ? 'No chats match that search.' : 'No chats yet. Your conversations are saved here.'}
        </p>
      ) : (
        <div className="space-y-3">
          {grouped.map((group) => (
            <div key={group.label}>
              <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.items.map((item) =>
                  renaming?.id === item.id ? (
                    <form
                      key={item.id}
                      onSubmit={submitRename}
                      className="flex items-center gap-1 rounded-lg border bg-background px-1.5 py-1"
                    >
                      <input
                        value={renaming.title}
                        onChange={(event) =>
                          setRenaming({ id: item.id, title: event.target.value })
                        }
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') setRenaming(null);
                        }}
                        autoFocus
                        maxLength={200}
                        aria-label="Chat title"
                        className="min-w-0 flex-1 bg-transparent px-1 text-xs outline-none"
                      />
                      <button
                        type="submit"
                        aria-label="Save title"
                        className="rounded p-1 text-emerald-600 hover:bg-accent"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label="Cancel rename"
                        onClick={() => setRenaming(null)}
                        className="rounded p-1 text-muted-foreground hover:bg-accent"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </form>
                  ) : (
                    <div
                      key={item.id}
                      className={cn(
                        'group flex items-center gap-0.5 rounded-lg pr-1 transition-colors',
                        item.id === activeId ? 'bg-primary/10' : 'hover:bg-accent/70',
                      )}
                    >
                      <button
                        onClick={() => openConversation(item.id)}
                        aria-current={item.id === activeId ? 'true' : undefined}
                        className="min-w-0 flex-1 px-2 py-1.5 text-left"
                      >
                        <span
                          className={cn(
                            'flex items-center gap-1 truncate text-[13px]',
                            item.id === activeId ? 'font-medium text-primary' : 'text-foreground',
                          )}
                        >
                          {pinned.includes(item.id) && (
                            <Pin className="h-3 w-3 shrink-0 text-primary" aria-hidden />
                          )}
                          <span className="truncate">{item.title}</span>
                        </span>
                        <span className="oa-caption block truncate">
                          {relativeTime(item.updated_at)} · {item.message_count} message
                          {item.message_count === 1 ? '' : 's'}
                        </span>
                      </button>
                      <button
                        onClick={() => setPinned((current) => togglePin(current, item.id))}
                        aria-label={
                          pinned.includes(item.id)
                            ? `Unpin chat “${item.title}”`
                            : `Pin chat “${item.title}”`
                        }
                        className={cn(
                          'rounded p-1 text-muted-foreground transition-opacity hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100',
                          pinned.includes(item.id) ? 'opacity-100' : 'opacity-0',
                        )}
                      >
                        {pinned.includes(item.id) ? (
                          <PinOff className="h-3.5 w-3.5" />
                        ) : (
                          <Pin className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <button
                        onClick={() => setRenaming({ id: item.id, title: item.title })}
                        aria-label={`Rename chat “${item.title}”`}
                        className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={() => void deleteConversation(item.id)}
                        aria-label={`Delete chat “${item.title}”`}
                        disabled={remove.isPending && remove.variables === item.id}
                        className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ),
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  const sidebar = (
    <>
      <div className="flex h-14 shrink-0 items-center gap-2 border-b border-border/70 px-3">
        <span className="oa-brand flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-white shadow-sm">
          <Sparkles className="h-3.5 w-3.5" aria-hidden />
        </span>
        <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold">Assistant</h2>
        <button
          onClick={startNewChat}
          aria-label="New chat"
          title="New chat"
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Plus className="h-4 w-4" />
        </button>
      </div>

      <div className="shrink-0 space-y-0.5 p-2">
        <button
          onClick={startNewChat}
          className="flex w-full items-center gap-2 rounded-lg border border-border/70 bg-background px-2.5 py-1.5 text-left text-[13px] font-medium transition-colors hover:border-primary/40 hover:bg-accent"
        >
          <MessagesSquare className="h-4 w-4 text-muted-foreground" aria-hidden /> New chat
        </button>
        <nav className="pt-1" aria-label="Assistant navigation">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <link.icon className="h-3.5 w-3.5" aria-hidden /> {link.label}
            </Link>
          ))}
        </nav>
      </div>

      <div className="shrink-0 px-2 pb-1">
        <label className="relative block">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search chats"
            aria-label="Search chats"
            className="w-full rounded-lg border border-border/70 bg-background py-1.5 pl-7 pr-2 text-xs outline-none focus:border-primary/60"
          />
        </label>
      </div>

      {historyList}
    </>
  );

  const isFresh = entries.length === 0;

  const composer = (
    <>
      {error && (
        <p
          className="mb-2 rounded-lg border border-destructive/30 bg-destructive/[0.06] px-3 py-1.5 text-xs text-destructive"
          role="alert"
        >
          {error}
        </p>
      )}
      <AssistantComposer
        value={draft}
        onChange={setDraft}
        onSend={(payload) => void send(payload)}
        onStop={() => abortRef.current?.abort()}
        busy={busy}
        large={isFresh}
        modelOverride={override}
        onModelOverride={setOverride}
        onVoiceActivity={robot.voiceActivity}
        autoFocus
        resources={resources}
        starters={STARTERS}
        mode={mode}
        onModeChange={setMode}
        footer={
          <p className="oa-caption mt-1.5 flex items-center gap-1.5 px-1">
            <span
              className={cn('h-1.5 w-1.5 rounded-full', ready ? 'bg-emerald-500' : 'bg-amber-500')}
              aria-hidden
            />
            {local
              ? 'Runs on this machine · actions are permission-checked'
              : 'Uses your configured endpoint · actions are permission-checked'}
          </p>
        }
      />
    </>
  );

  return (
    <div className="flex h-[calc(100dvh-4rem)] w-full overflow-hidden">
      {historyOpen && (
        <aside className="hidden w-64 shrink-0 flex-col border-r border-border/70 bg-sidebar/40 lg:flex xl:w-72">
          {sidebar}
        </aside>
      )}

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2.5 border-b border-border/70 bg-card/70 px-3 backdrop-blur sm:px-4">
          <button
            onClick={() => setMobileHistoryOpen(true)}
            aria-label="Open chat history"
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
          >
            <MessagesSquare className="h-4 w-4" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[13px] font-semibold leading-tight">
              {activeId ? activeTitle || 'Assistant' : 'AI assistant'}
            </h1>
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
          {!isFresh && (
            <button
              onClick={exportConversation}
              aria-label="Export this chat"
              title="Export this chat as Markdown"
              className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <Download className="h-4 w-4" />
            </button>
          )}
          <button
            onClick={() => setRobotOpen(!robotOpen)}
            aria-label={robotOpen ? 'Hide the 3D companion' : 'Show the 3D companion'}
            aria-pressed={robotOpen}
            title={robotOpen ? 'Hide the 3D companion' : 'Show the 3D companion'}
            className="hidden rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground xl:inline-flex"
          >
            <Bot className="h-4 w-4" />
          </button>
          <button
            onClick={() => setMobileRobotOpen(true)}
            aria-label="Open the 3D companion"
            title="Open the 3D companion"
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground xl:hidden"
          >
            <Bot className="h-4 w-4" />
          </button>
          <button
            onClick={() => setHistoryOpen((open) => !open)}
            aria-label={historyOpen ? 'Hide chat history' : 'Show chat history'}
            aria-expanded={historyOpen}
            title={historyOpen ? 'Hide chat history' : 'Show chat history'}
            className="hidden rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground lg:inline-flex"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        </header>

        <div
          ref={scrollRef}
          className="oa-dot-grid oa-scroll min-h-0 flex-1 overflow-y-auto"
        >
          {entries.length === 0 ? (
            <div className="flex min-h-full items-center justify-center px-4 py-8">
              <div className="w-full max-w-2xl space-y-5">
                <div className="text-center">
                  <h2 className="flex items-center justify-center gap-2 text-xl font-semibold tracking-tight sm:text-2xl">
                    <Sparkles className="h-5 w-5 text-primary" aria-hidden />
                    What do you want to automate?
                  </h2>
                  <p className="mx-auto mt-1.5 max-w-lg text-sm text-muted-foreground">
                    Describe the outcome in plain language. I can build a workflow, create an
                    agent, run or publish automations, and explain what happened.
                  </p>
                </div>

                {status.data && !status.data.ready && (
                  <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2.5">
                    <p className="text-xs text-muted-foreground">{status.data.install_hint}</p>
                    <Link
                      href="/settings/models"
                      className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                    >
                      <Radio className="h-3 w-3" aria-hidden /> Connect a model
                    </Link>
                  </div>
                )}

                <div className="flex flex-wrap justify-center gap-1.5">
                  {QUICK_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => setDraft(prompt)}
                      disabled={busy}
                      className="rounded-full border border-border/70 bg-background/80 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>

                {(detail.isLoading || conversations.isLoading) && activeId && (
                  <p className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Opening chat…
                  </p>
                )}

                <div className="pt-1">{composer}</div>
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl space-y-4 px-3 py-5 sm:px-6">
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
                      'max-w-[92%] break-words rounded-2xl px-3.5 py-2.5 text-sm leading-6',
                      entry.role === 'user'
                        ? 'whitespace-pre-wrap rounded-br-md bg-primary text-primary-foreground'
                        : 'rounded-bl-md border border-border/70 bg-card',
                    )}
                  >
                    {entry.role === 'assistant' ? (
                      <Markdown content={entry.content} />
                    ) : (
                      entry.content
                    )}
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

        {!isFresh && (
          <div className="shrink-0 border-t border-border/70 bg-background px-3 py-3 sm:px-6">
            <div className="mx-auto w-full max-w-3xl">{composer}</div>
          </div>
        )}
      </section>

      {robotOpen && (
        <aside className="relative hidden w-[320px] shrink-0 border-l border-border/70 xl:flex 2xl:w-[350px]">
          <RobotCompanion controller={robot} />
        </aside>
      )}

      {mobileRobotOpen && (
        <div className="fixed inset-x-0 bottom-0 top-16 z-50 xl:hidden">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileRobotOpen(false)}
            aria-hidden
          />
          <div className="relative h-full w-full overflow-hidden">
            <RobotCompanion
              controller={robot}
              className="h-full"
              onClose={() => setMobileRobotOpen(false)}
            />
          </div>
        </div>
      )}

      {mobileHistoryOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileHistoryOpen(false)}
            aria-hidden
          />
          <aside className="relative flex h-full w-72 flex-col border-r border-border bg-background">
            {sidebar}
            <button
              onClick={() => setMobileHistoryOpen(false)}
              aria-label="Close chat history"
              className="absolute right-2 top-3 rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </aside>
        </div>
      )}
    </div>
  );
}
