'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react';
import { Layout } from '@/components/layout';
import { Protected } from '@/components/protected';
import { PageHeader } from '@/components/ui/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/form';
import { DataTable, type Column } from '@/components/ui/table';
import { StatusBadge } from '@/components/ui/status';
import { PermissionGate } from '@/components/PermissionGate';
import { CreateOrchestrationDialog } from '@/features/orchestrations/CreateOrchestrationDialog';
import { useOrchestrations } from '@/features/orchestrations/api';
import { TERMINAL_RUN_STATUSES, type OrchestrationSummary } from '@/features/orchestrations/types';
import { useSearchParamsState } from '@/lib/queries';

const PAGE_SIZE = 10;
const ACTIVE_STATUSES = ['planning', 'ready', 'running', 'waiting', 'paused'];
const ATTENTION_STATUSES = ['failed', 'partially_succeeded', 'timed_out', 'cancelled'];

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function BudgetMeter({
  label,
  used,
  limit,
  format,
}: {
  label: string;
  used: number;
  limit: number;
  format: (value: number) => string;
}) {
  const pct = limit > 0 ? Math.min(100, Math.max(0, (used / limit) * 100)) : 0;
  return (
    <div className="min-w-36 space-y-1">
      <div className="flex justify-between gap-3 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">{format(used)}{limit > 0 ? ` / ${format(limit)}` : ''}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={pct >= 90 ? 'h-full rounded-full bg-destructive' : 'h-full rounded-full bg-primary'}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  icon,
  tone = 'default',
}: {
  label: string;
  value: React.ReactNode;
  hint: string;
  icon: React.ReactNode;
  tone?: 'default' | 'success' | 'warning' | 'active';
}) {
  const tones = {
    default: 'text-foreground',
    success: 'text-emerald-500',
    warning: 'text-amber-500',
    active: 'text-primary',
  };
  return (
    <div className="rounded-xl border border-border/70 bg-card p-4 shadow-card">
      <div className="flex items-center justify-between text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
        <span className={tones[tone]}>{icon}</span>
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

export default function OrchestrationsPage() {
  const [search, setSearch] = React.useState('');
  const debouncedSearch = useSearchParamsState(search);
  const [status, setStatus] = React.useState('');
  const [page, setPage] = React.useState(1);
  const [createOpen, setCreateOpen] = React.useState(false);
  const { items, total, isLoading, error, refetch } = useOrchestrations({
    status: status || undefined,
    search: debouncedSearch || undefined,
    page,
    pageSize: PAGE_SIZE,
  });

  React.useEffect(() => {
    setPage(1);
  }, [debouncedSearch, status]);

  const active = items.filter((run) => ACTIVE_STATUSES.includes(run.status)).length;
  const completed = items.filter((run) => TERMINAL_RUN_STATUSES.includes(run.status)).length;
  const attention = items.filter((run) => ATTENTION_STATUSES.includes(run.status)).length;

  const columns: Column<OrchestrationSummary>[] = [
    {
      key: 'objective',
      header: 'Objective',
      accessor: (r) => r.objective,
      render: (r) => (
        <div className="max-w-xl">
          <Link
            href={`/orchestrations/${r.id}`}
            className="line-clamp-2 font-medium underline-offset-4 hover:text-primary hover:underline"
          >
            {r.objective}
          </Link>
          <p className="mt-1 text-xs text-muted-foreground">
            Created {new Date(r.created_at).toLocaleString()}
          </p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (r) => (
        <div className="space-y-1">
          <StatusBadge status={r.status} />
          <p className="text-xs text-muted-foreground">
            Updated {new Date(r.updated_at).toLocaleString()}
          </p>
        </div>
      ),
    },
    {
      key: 'budget',
      header: 'Resource usage',
      render: (r) => (
        <div className="space-y-2">
          <BudgetMeter
            label="Tokens"
            used={asNumber(r.usage?.tokens)}
            limit={asNumber(r.budget?.max_total_tokens)}
            format={(value) => value.toLocaleString()}
          />
          <BudgetMeter
            label="Cost"
            used={asNumber(r.usage?.cost)}
            limit={asNumber(r.budget?.max_total_cost)}
            format={(value) => `$${value.toFixed(2)}`}
          />
        </div>
      ),
    },
    {
      key: 'actions',
      header: '',
      className: 'text-right',
      render: (r) => (
        <Link
          href={`/orchestrations/${r.id}`}
          aria-label={`Open orchestration: ${r.objective}`}
          className="inline-flex items-center gap-1 rounded-md border border-input px-3 py-2 text-xs font-medium hover:bg-accent"
        >
          Open <ArrowUpRight className="h-3.5 w-3.5" />
        </Link>
      ),
    },
  ];

  return (
    <Protected>
      <Layout>
        <div className="oa-page">
          <PageHeader
            title="Orchestrations"
            description="Plan, run, and supervise teams of specialized agents with budgets, live task graphs, and failure recovery."
            breadcrumbs={[{ label: 'Home', href: '/' }, { label: 'Orchestrations' }]}
            actions={
              <PermissionGate permission="agent:create">
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus className="mr-2 h-4 w-4" /> New orchestration
                </Button>
              </PermissionGate>
            }
          />

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Total runs"
              value={total.toLocaleString()}
              hint="Matching the current filters"
              icon={<Clock3 className="h-4 w-4" />}
            />
            <StatCard
              label="Active"
              value={active}
              hint="Planning, ready, running, or paused on this page"
              icon={<Activity className="h-4 w-4" />}
              tone="active"
            />
            <StatCard
              label="Finished"
              value={completed}
              hint="Terminal runs on this page"
              icon={<CheckCircle2 className="h-4 w-4" />}
              tone="success"
            />
            <StatCard
              label="Needs attention"
              value={attention}
              hint="Failed, cancelled, or timed out on this page"
              icon={<AlertTriangle className="h-4 w-4" />}
              tone="warning"
            />
          </div>

          <div className="flex flex-col gap-2 rounded-xl border border-border/70 bg-card p-2.5 shadow-card sm:flex-row">
            <div className="relative w-full max-w-sm">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search objectives…"
                aria-label="Search orchestrations"
                className="pl-9"
              />
            </div>
            <Select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Filter by status"
              className="rounded-lg border-transparent bg-muted/60 sm:w-48"
            >
              <option value="">All statuses</option>
              <option value="created">Created</option>
              <option value="planning">Planning</option>
              <option value="ready">Ready</option>
              <option value="running">Running</option>
              <option value="waiting">Waiting</option>
              <option value="paused">Paused</option>
              <option value="succeeded">Succeeded</option>
              <option value="partially_succeeded">Partially succeeded</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
              <option value="timed_out">Timed out</option>
            </Select>
            <Button variant="outline" size="sm" className="sm:ml-auto" onClick={() => refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" /> Refresh
            </Button>
          </div>

          <DataTable
            columns={columns}
            rows={items}
            keyOf={(r) => r.id}
            loading={isLoading}
            error={error}
            onRetry={() => refetch()}
            emptyTitle={search || status ? 'No matching orchestrations' : 'No orchestrations yet'}
            emptyDescription={
              search || status
                ? 'Adjust the search or status filter to see more runs.'
                : 'Create an objective, choose a team strategy, and let the planner build the task graph.'
            }
            emptyAction={
              <PermissionGate permission="agent:create">
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus className="mr-2 h-4 w-4" /> New orchestration
                </Button>
              </PermissionGate>
            }
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
          />
        </div>
      </Layout>
      <CreateOrchestrationDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </Protected>
  );
}