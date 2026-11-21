'use client';

import * as React from 'react';
import Link from 'next/link';
import { useWorkflowExecutions } from '@/features/workflows/workflows-api';
import { StatusBadge } from '@/components/ui/status';
import { Button } from '@/components/ui/button';
import { RefreshCw, Play, ExternalLink, Clock3 } from 'lucide-react';
import { cn } from '@/lib/utils';

function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return '—';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function formatWhen(value?: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** n8n-style execution log for a single workflow. */
export function ExecutionLog({ workflowId }: { workflowId: string }) {
  const { items, total, isLoading, refetch } = useWorkflowExecutions(workflowId);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold leading-tight">Executions</h2>
          <p className="text-[11px] text-muted-foreground">
            {total} recorded run{total === 1 ? '' : 's'} for this workflow
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isLoading}>
            <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', isLoading && 'animate-spin')} />
            Refresh
          </Button>
          <Link
            href="/runs"
            className="inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
          >
            All runs <ExternalLink className="h-3 w-3" aria-hidden />
          </Link>
        </div>
      </div>

      <div className="oa-scroll min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">Loading executions…</p>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <Play className="h-4 w-4" aria-hidden />
            </span>
            <p className="text-sm font-medium">No executions yet</p>
            <p className="max-w-sm text-xs text-muted-foreground">
              Run the workflow with <span className="oa-code">Test workflow</span> to see node-level
              results here. Published triggers record runs automatically.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-card">
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Execution</th>
                <th className="px-3 py-2 font-medium">Trigger</th>
                <th className="px-3 py-2 font-medium">Started</th>
                <th className="px-3 py-2 font-medium">Duration</th>
                <th className="px-4 py-2 font-medium">Error</th>
              </tr>
            </thead>
            <tbody>
              {items.map((run) => (
                <tr key={run.id} className="border-b border-border/60 last:border-0 hover:bg-accent/40">
                  <td className="px-4 py-2">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-3 py-2">
                    <span className="oa-code text-[11px]">{run.id.slice(0, 10)}</span>
                  </td>
                  <td className="px-3 py-2 text-xs capitalize text-muted-foreground">{run.trigger_type}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <Clock3 className="h-3 w-3" aria-hidden /> {formatWhen(run.started_at ?? run.created_at)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs tabular-nums text-muted-foreground">
                    {formatDuration(run.started_at, run.completed_at)}
                  </td>
                  <td className="max-w-[260px] truncate px-4 py-2 text-xs text-destructive" title={run.error_message ?? undefined}>
                    {run.error_message ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
