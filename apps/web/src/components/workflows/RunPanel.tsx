'use client';

import * as React from 'react';
import {
  Bug,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Clock3,
  Loader2,
  Play,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { NodeRunView, WorkflowRunResult, WorkflowRunSummary } from '@/features/workflows/workflows-api';

// n8n-style execution panel: one row per node with status, timing, inputs and
// outputs. Reads the synchronous run response, so there is no polling.

const STATUS: Record<
  string,
  { label: string; className: string; icon: React.ComponentType<{ className?: string }> }
> = {
  succeeded: { label: 'Succeeded', className: 'text-emerald-600 dark:text-emerald-400', icon: CheckCircle2 },
  failed: { label: 'Failed', className: 'text-destructive', icon: XCircle },
  skipped: { label: 'Skipped', className: 'text-muted-foreground', icon: CircleSlash },
  waiting: { label: 'Waiting', className: 'text-amber-600 dark:text-amber-400', icon: Clock3 },
};

function statusOf(status: string) {
  return STATUS[status] ?? { label: status, className: 'text-muted-foreground', icon: Clock3 };
}

function formatMs(ms?: number | null) {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  if (value === undefined || value === null) return null;
  return (
    <div>
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <pre className="max-h-56 overflow-auto rounded-md border bg-muted/40 p-2 text-[11px] leading-relaxed">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function RunRow({
  run,
  onSelectNode,
  onDebugNode,
  onAskKoro,
}: {
  run: NodeRunView;
  onSelectNode?: (id: string) => void;
  onDebugNode?: (id: string) => void;
  onAskKoro?: (run: NodeRunView) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const meta = run.status === 'failed' && run.continued
    ? { label: 'Failed · continued', className: 'text-amber-600 dark:text-amber-400' }
    : statusOf(run.status);
  const Icon = run.status === 'failed' && run.continued ? XCircle : statusOf(run.status).icon;
  return (
    <li className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-accent/60"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        <Icon className={cn('h-4 w-4 shrink-0', meta.className)} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{run.node_name}</span>
        <span className="hidden rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground sm:inline">
          {run.node_type}
        </span>
        <span className={cn('w-20 text-right text-xs', meta.className)}>{meta.label}</span>
        <span className="w-16 text-right text-xs text-muted-foreground">{formatMs(run.duration_ms)}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t p-2.5">
          {run.reason && (
            <p className="text-xs text-muted-foreground">
              Reason: <span className="oa-code">{run.reason}</span>
            </p>
          )}
          {run.error && (
            <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
              {run.error}
              {run.error_code ? ` (${run.error_code})` : ''}
              {run.continued ? ' — continued with the node\u2019s input' : ''}
            </p>
          )}
          {(run.attempt ?? 1) > 1 && (
            <p className="text-xs text-muted-foreground">Attempts: {run.attempt}</p>
          )}
          <JsonBlock label="Input" value={run.inputs} />
          <JsonBlock label="Output" value={run.outputs} />
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span className="oa-code">{run.node_id}</span>
            <span className="flex items-center gap-2">
              {run.status === 'failed' && onAskKoro && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-[11px]"
                  onClick={() => onAskKoro(run)}
                >
                  <Sparkles className="mr-1.5 h-3 w-3 text-primary" aria-hidden />
                  Ask Koro to fix
                </Button>
              )}
              {onDebugNode && (
                <button
                  type="button"
                  onClick={() => onDebugNode(run.node_id)}
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  <Bug className="h-3 w-3" aria-hidden />
                  {run.status === 'failed' ? 'Debug in editor' : 'Inspect data'}
                </button>
              )}
              <button
                type="button"
                onClick={() => onSelectNode?.(run.node_id)}
                className="text-primary hover:underline"
              >
                Select on canvas
              </button>
            </span>
          </div>
        </div>
      )}
    </li>
  );
}

function SummaryChips({ summary }: { summary?: WorkflowRunSummary }) {
  if (!summary) return null;
  const chips = [
    { label: 'succeeded', value: summary.succeeded, className: 'text-emerald-600 dark:text-emerald-400' },
    { label: 'failed', value: summary.failed, className: 'text-destructive' },
    { label: 'skipped', value: summary.skipped, className: 'text-muted-foreground' },
    { label: 'waiting', value: summary.waiting, className: 'text-amber-600 dark:text-amber-400' },
    ...(summary.warnings
      ? [{ label: 'warnings', value: summary.warnings, className: 'text-amber-600 dark:text-amber-400' }]
      : []),
  ];
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs">
      {chips.map((chip) => (
        <span key={chip.label} className={chip.className}>
          {chip.value} {chip.label}
        </span>
      ))}
      <span className="text-muted-foreground">· {formatMs(summary.duration_ms)}</span>
    </span>
  );
}

export function RunPanel({
  result,
  running,
  error,
  onClose,
  onSelectNode,
  onDebugNode,
  onRerun,
  onAskKoro,
}: {
  result: WorkflowRunResult | null;
  running: boolean;
  error: string | null;
  onClose: () => void;
  onSelectNode?: (id: string) => void;
  /** Opens the node detail view on this run's input and output. */
  onDebugNode?: (id: string) => void;
  onRerun: (input: Record<string, unknown>) => void;
  /** Opens Koro with a debugging prompt for the failed step. */
  onAskKoro?: (run: NodeRunView) => void;
}) {
  const [inputText, setInputText] = React.useState('{}');
  const [inputError, setInputError] = React.useState<string | null>(null);
  const [showOutput, setShowOutput] = React.useState(false);

  const rerun = () => {
    try {
      const parsed = inputText.trim() ? JSON.parse(inputText) : {};
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setInputError('Trigger input must be a JSON object');
        return;
      }
      setInputError(null);
      onRerun(parsed as Record<string, unknown>);
    } catch {
      setInputError('Trigger input is not valid JSON');
    }
  };

  const overall = result ? statusOf(result.status === 'completed' ? 'succeeded' : result.status) : null;
  const OverallIcon = overall?.icon;

  return (
    <section
      className="flex h-full min-h-0 flex-col overflow-hidden border-t border-border bg-card"
      aria-label="Workflow test run results"
      data-testid="run-panel"
    >
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border/70 bg-muted/20 px-3 py-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <Play className="h-3.5 w-3.5 text-primary" aria-hidden /> Test run
        </h2>
        {running && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running…
          </span>
        )}
        {!running && result && (
          <>
            <span className={cn('flex items-center gap-1.5 text-xs font-medium', overall?.className)}>
              {OverallIcon && <OverallIcon className="h-3.5 w-3.5" />}
              {result.status}
            </span>
            <SummaryChips summary={result.summary} />
            <span className="oa-code hidden text-[11px] text-muted-foreground md:inline">
              {result.execution_id.slice(0, 8)}
            </span>
          </>
        )}
        {!running && !result && !error && (
          <span className="text-xs text-muted-foreground">
            Runs the current canvas — no save or publish needed.
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close run panel"
          className="ml-auto rounded p-1 hover:bg-accent"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="oa-scroll min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-sm text-destructive" role="alert">
            {error}
          </p>
        )}

        {result && (
          <div className="space-y-3">
            {result.error_message && (
              <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                {result.error_message}
              </p>
            )}

            {result.node_runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nothing ran. Add a trigger and at least one node to the canvas.
              </p>
            ) : (
              <ul className="max-h-[320px] space-y-1.5 overflow-auto pr-1">
                {result.node_runs.map((run) => (
                  <RunRow
                    key={`${run.node_id}:${run.started_at ?? ''}`}
                    run={run}
                    onSelectNode={onSelectNode}
                    onDebugNode={onDebugNode}
                    onAskKoro={onAskKoro}
                  />
                ))}
              </ul>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setShowOutput((v) => !v)}
                className="text-xs text-primary hover:underline"
                aria-expanded={showOutput}
              >
                {showOutput ? 'Hide final output' : 'Show final output'}
              </button>
            </div>
            {showOutput && (
              <pre className="max-h-52 overflow-auto rounded-md border bg-muted/40 p-2 text-[11px]">
                {JSON.stringify(result.output ?? {}, null, 2)}
              </pre>
            )}
          </div>
        )}

        <div className="rounded-md border bg-muted/20 p-2">
          <label htmlFor="run-trigger-input" className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Trigger input (JSON)
          </label>
          <textarea
            id="run-trigger-input"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            rows={2}
            spellCheck={false}
            className="mt-1 w-full resize-y rounded-md border bg-background px-2 py-1.5 font-mono text-xs outline-none focus:border-primary"
          />
          {inputError && <p className="mt-1 text-xs text-destructive">{inputError}</p>}
          <div className="mt-2 flex justify-end">
            <Button size="sm" variant="outline" onClick={rerun} loading={running}>
              <Play className="mr-1.5 h-3.5 w-3.5" />
              Run again
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
