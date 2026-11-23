'use client';

import * as React from 'react';
import {
  ArrowDownToLine,
  Check,
  Copy,
  Database,
  Clock3,
  Pin,
  PinOff,
  AlertTriangle,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { nodeMeta, triggerMeta } from '@/features/workflows/node-catalog';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import type { PinnedDatum } from '@/features/workflows/types';
import type { WorkflowNodeRunResult } from '@/features/workflows/workflows-api';
import {
  itemJson,
  resolveNodeInputs,
  resolveNodeOutput,
  type DataItem,
  type InputSource,
  type NodeOutputData,
  type NodeRunLike,
} from '@/features/workflows/node-data';
import { Inspector } from './Inspector';

// n8n-style NDV: input data on the left, the parameter form in the middle,
// output data on the right. Pinning freezes an output inside the definition so
// it survives reloads, undo and Koro edits; it never affects real executions.

const STATUS_TONE: Record<string, string> = {
  succeeded: 'text-emerald-600 dark:text-emerald-400',
  failed: 'text-destructive',
  waiting: 'text-amber-600 dark:text-amber-400',
  skipped: 'text-muted-foreground',
};

function formatDuration(ms?: number | null) {
  if (ms === null || ms === undefined) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={copied ? 'Copied' : label}
      onClick={async () => {
        try {
          await navigator.clipboard?.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1400);
        } catch {
          /* clipboard unavailable — the JSON is still selectable */
        }
      }}
      className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function ItemList({ items, empty }: { items: DataItem[]; empty: React.ReactNode }) {
  if (items.length === 0) {
    return <div className="px-3 py-6 text-center text-xs text-muted-foreground">{empty}</div>;
  }
  return (
    <ul className="space-y-2 p-2">
      {items.map((item) => (
        <li key={item.index} className="overflow-hidden rounded-lg border bg-background">
          <div className="flex items-center justify-between border-b bg-muted/40 px-2 py-1">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Item {item.index}
            </span>
            <CopyButton text={itemJson(item)} label={`Copy item ${item.index}`} />
          </div>
          <pre className="oa-scroll max-h-72 overflow-auto whitespace-pre-wrap break-words p-2.5 font-mono text-[11px] leading-relaxed">
            {itemJson(item)}
          </pre>
        </li>
      ))}
    </ul>
  );
}

function PaneHeader({
  title,
  count,
  hint,
  children,
}: {
  title: string;
  /** Item count; omit for panes that do not list items. */
  count?: number;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b bg-muted/20 px-3 py-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      {count !== undefined && (
        <span className="rounded-full bg-muted px-1.5 text-[10px] font-medium tabular-nums text-muted-foreground">
          {count} item{count === 1 ? '' : 's'}
        </span>
      )}
      {hint && <span className="text-[10px] text-muted-foreground">{hint}</span>}
      <div className="ml-auto flex items-center gap-1">{children}</div>
    </div>
  );
}

function InputSourceChip({
  source,
  active,
  onClick,
}: {
  source: InputSource;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-active={active}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] transition-colors',
        active
          ? 'border-primary/50 bg-primary/10 text-foreground'
          : 'border-border bg-card text-muted-foreground hover:bg-accent',
      )}
    >
      <span className="truncate">{source.label}</span>
      {source.source === 'pinned' && (
        <Pin className="h-3 w-3 shrink-0 text-amber-600 dark:text-amber-400" aria-label="Pinned" />
      )}
    </button>
  );
}

export function NodeDetailView({
  editor,
  open,
  onClose,
  readOnly = false,
  workflowId,
  run,
  runs = [],
  onTestStep,
}: {
  editor: WorkflowEditor;
  open: boolean;
  onClose: () => void;
  readOnly?: boolean;
  workflowId?: string;
  /** Most recent data for the selected node (step test wins over workflow run). */
  run?: NodeRunLike | null;
  /** All node runs from the last workflow test. */
  runs?: NodeRunLike[];
  onTestStep?: (nodeId: string, input: Record<string, unknown>) => Promise<WorkflowNodeRunResult>;
}) {
  const { definition, selection } = editor;
  const [sourceIndex, setSourceIndex] = React.useState(0);
  const closeRef = React.useRef<HTMLButtonElement>(null);

  const itemId = selection?.kind === 'node' || selection?.kind === 'trigger' ? selection.id : null;

  React.useEffect(() => {
    setSourceIndex(0);
  }, [itemId]);

  React.useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);

  const inputs = React.useMemo(() => {
    if (!itemId) return [];
    return resolveNodeInputs({
      definition,
      nodeId: itemId,
      run,
      runs,
      pinned: definition.settings.pinned_data,
    });
  }, [definition, itemId, run, runs]);

  const pinned: PinnedDatum | undefined =
    itemId ? definition.settings.pinned_data?.[itemId] : undefined;

  const output: NodeOutputData = React.useMemo(
    () => resolveNodeOutput({ run, pinned }),
    [run, pinned],
  );

  if (!open || !itemId) return null;

  const trigger = selection?.kind === 'trigger'
    ? definition.triggers.find((t) => t.id === itemId)
    : undefined;
  const node = selection?.kind === 'node'
    ? definition.nodes.find((n) => n.id === itemId)
    : undefined;
  const item = trigger ?? node;
  if (!item) return null;

  const meta = trigger ? triggerMeta(trigger.type) : nodeMeta(node!.type);
  const Icon = meta.icon;
  const source = inputs[Math.min(sourceIndex, Math.max(inputs.length - 1, 0))];
  const testInput = source && source.items.length > 0
    ? (source.items.length === 1
        ? (source.items[0].json as Record<string, unknown>)
        : { items: source.items.map((i) => i.json) })
    : undefined;

  const canPin = !readOnly && output.source === 'run' && output.items.length > 0;
  const duration = formatDuration(output.duration_ms);

  return (
    <div
      className="fixed inset-0 z-40 flex items-stretch justify-center bg-foreground/20 p-0 backdrop-blur-[2px] sm:p-4"
      role="presentation"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`${item.name} detail`}
        className="flex h-full min-h-0 w-full max-w-[1280px] flex-col overflow-hidden border-border bg-card shadow-2xl sm:rounded-2xl sm:border"
        onPointerDown={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-2.5 border-b px-3 py-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border/50">
            <Icon className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold leading-tight">{item.name}</h2>
            <p className="truncate text-[11px] capitalize text-muted-foreground">
              {trigger ? `Trigger · ${trigger.type}` : `Node · ${node!.type}`}
              {node?.disabled ? ' · disabled' : ''}
            </p>
          </div>
          {output.status && (
            <span
              className={cn(
                'ml-1 shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
                STATUS_TONE[output.status] ?? 'text-muted-foreground',
              )}
            >
              {output.status}
            </span>
          )}
          {duration && (
            <span className="hidden shrink-0 text-[11px] text-muted-foreground sm:inline">{duration}</span>
          )}
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={onClose} className="h-8">
              Close
            </Button>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="Close node detail"
              className="oa-icon-button"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)_minmax(0,1fr)] lg:overflow-hidden">
          {/* Input data */}
          <div className="flex min-h-0 flex-col border-b lg:border-b-0 lg:border-r">
            <PaneHeader title="Input" count={source?.items.length ?? 0}>
              {source?.source === 'pinned' && (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                  <Pin className="h-3 w-3" aria-hidden /> pinned
                </span>
              )}
            </PaneHeader>
            {inputs.length > 1 && (
              <div className="flex shrink-0 flex-wrap gap-1 border-b bg-muted/10 px-2 py-1.5">
                {inputs.map((s, i) => (
                  <InputSourceChip
                    key={`${s.from_id ?? s.label}:${i}`}
                    source={s}
                    active={i === Math.min(sourceIndex, inputs.length - 1)}
                    onClick={() => setSourceIndex(i)}
                  />
                ))}
              </div>
            )}
            <div className="oa-scroll min-h-0 flex-1 overflow-y-auto">
              <ItemList
                items={source?.items ?? []}
                empty={
                  trigger
                    ? 'Triggers receive their data from the event, not from an upstream node.'
                    : 'No input data yet. Run the workflow, or use Test step in the parameters pane.'
                }
              />
            </div>
          </div>

          {/* Parameters */}
          <div className="flex min-h-0 flex-col border-b lg:border-b-0 lg:border-r">
            <PaneHeader title="Parameters" hint="edits are undoable" />
            <div className="min-h-0 flex-1">
              <Inspector
                editor={editor}
                disabled={readOnly}
                workflowId={workflowId}
                onTestStep={onTestStep}
                embedded
                testInput={testInput}
              />
            </div>
          </div>

          {/* Output data */}
          <div className="flex min-h-0 flex-col">
            <PaneHeader title="Output" count={output.items.length}>
              {!readOnly && pinned && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => editor.unpinData(itemId)}
                  title="Remove pinned output"
                >
                  <PinOff className="mr-1 h-3 w-3" aria-hidden />
                  Unpin
                </Button>
              )}
              {canPin && !pinned && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px]"
                  onClick={() =>
                    editor.pinData(itemId, {
                      pinned_at: new Date().toISOString(),
                      outputs: run?.outputs ?? {},
                      inputs: run?.inputs ?? null,
                      status: run?.status,
                    })
                  }
                  title="Keep this output for previews and step tests"
                >
                  <Pin className="mr-1 h-3 w-3" aria-hidden />
                  Pin output
                </Button>
              )}
            </PaneHeader>

            {output.newer_run && (
              <p className="flex items-center gap-1.5 border-b bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
                Showing pinned data. A newer run exists — unpin to see it.
              </p>
            )}
            {output.error && (
              <p className="border-b bg-destructive/5 px-3 py-1.5 text-[11px] text-destructive" role="alert">
                {output.error}
              </p>
            )}
            {!output.error && output.reason && (
              <p className="border-b bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
                Skipped: <span className="oa-code">{output.reason}</span>
              </p>
            )}

            <div className="oa-scroll min-h-0 flex-1 overflow-y-auto">
              <ItemList
                items={output.items}
                empty={
                  trigger
                    ? 'Run the trigger to see the event payload it produces.'
                    : 'No output yet. Use Test step, or run the workflow, then pin what you need.'
                }
              />
            </div>

            <footer className="shrink-0 border-t bg-muted/10 px-3 py-2 text-[11px] text-muted-foreground">
              {pinned ? (
                <span className="flex flex-wrap items-center gap-1.5">
                  <Pin className="h-3 w-3" aria-hidden />
                  Pinned {new Date(pinned.pinned_at).toLocaleString()} · saved with the workflow · executions ignore it
                </span>
              ) : (
                <span className="flex flex-wrap items-center gap-1.5">
                  <Database className="h-3 w-3" aria-hidden />
                  Output is shown after a run; pin it to keep it across edits and reloads.
                </span>
              )}
            </footer>
          </div>
        </div>

        <footer className="flex shrink-0 flex-wrap items-center gap-3 border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          <span className="oa-code">{item.id}</span>
          {output.items.length > 0 && (
            <span className="inline-flex items-center gap-1">
              <ArrowDownToLine className="h-3 w-3" aria-hidden />
              {output.items.length} output item{output.items.length === 1 ? '' : 's'}
            </span>
          )}
          {run?.status === 'succeeded' && (
            <span className="inline-flex items-center gap-1">
              <Clock3 className="h-3 w-3" aria-hidden />
              last data from this session
            </span>
          )}
          <span className="ml-auto hidden sm:inline">Esc closes</span>
        </footer>
      </section>
    </div>
  );
}
