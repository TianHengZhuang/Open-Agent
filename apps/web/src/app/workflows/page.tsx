'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Layout } from '@/components/layout';
import { Protected } from '@/components/protected';
import { PageHeader } from '@/components/ui/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, Switch } from '@/components/ui/form';
import { DataTable, type Column } from '@/components/ui/table';
import { StatusBadge } from '@/components/ui/status';
import { ConfirmDialog } from '@/components/ui/dialog';
import { CreateWorkflowDialog } from '@/components/workflows/CreateWorkflowDialog';
import { GenerateWithAiDialog } from '@/components/workflows/GenerateWithAiDialog';
import { PermissionGate } from '@/components/PermissionGate';
import { useToast } from '@/components/ui/toast';
import { toUserMessage } from '@/lib/api';
import { useSearchParamsState } from '@/lib/queries';
import { useWorkflows, useWorkflowMutations } from '@/features/workflows/workflows-api';
import type { WorkflowRecord } from '@/features/workflows/types';
import { Plus, Search, Pencil, Copy, Trash2, GitBranch, Sparkles } from 'lucide-react';

export default function WorkflowsPage() {
  const [q, setQ] = React.useState('');
  const [status, setStatus] = React.useState('');
  const [createOpen, setCreateOpen] = React.useState(false);
  const [generateOpen, setGenerateOpen] = React.useState(false);
  const [deleting, setDeleting] = React.useState<WorkflowRecord | null>(null);
  const debounced = useSearchParamsState(q);
  const { items, total, isLoading, error, refetch } = useWorkflows(
    debounced || status ? { search: debounced || undefined, status: status || undefined } : undefined,
  );
  const { duplicate, remove, publish, unpublish } = useWorkflowMutations();
  const [togglingId, setTogglingId] = React.useState<string | null>(null);

  // n8n's Active switch: publishing makes webhooks/schedules live, unpublishing
  // takes them offline without touching the saved versions.
  const onToggleActive = async (wf: WorkflowRecord, next: boolean) => {
    setTogglingId(wf.id);
    try {
      await (next ? publish : unpublish).mutateAsync(wf.id);
      toast({
        kind: 'success',
        title: next ? `“${wf.name}” is active` : `“${wf.name}” is inactive`,
        description: next ? 'Its production webhook is live.' : 'Its production webhook stopped listening.',
      });
    } catch (e) {
      toast({ kind: 'error', title: 'Could not change activation', description: toUserMessage(e) });
    } finally {
      setTogglingId(null);
    }
  };
  const { toast } = useToast();
  const router = useRouter();

  const onDuplicate = async (wf: WorkflowRecord) => {
    try {
      const copy = await duplicate.mutateAsync(wf.id);
      toast({ kind: 'success', title: `Duplicated as “${copy.name}”` });
      router.push(`/workflows/${copy.id}`);
    } catch (e) {
      toast({ kind: 'error', title: 'Duplicate failed', description: toUserMessage(e) });
    }
  };

  const onDelete = async () => {
    if (!deleting) return;
    try {
      await remove.mutateAsync(deleting.id);
      toast({ kind: 'success', title: `Deleted “${deleting.name}”` });
      setDeleting(null);
    } catch (e) {
      toast({ kind: 'error', title: 'Delete failed', description: toUserMessage(e) });
    }
  };

  const columns: Column<WorkflowRecord>[] = [
    {
      key: 'name', header: 'Name', sortable: true, accessor: (r) => r.name,
      render: (r) => (
        <span className="flex items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <GitBranch className="h-4 w-4" aria-hidden />
          </span>
          <span className="min-w-0">
            <Link href={`/workflows/${r.id}`} className="block truncate font-medium text-foreground hover:text-primary hover:underline">{r.name}</Link>
            <span className="oa-caption block truncate">{r.slug}</span>
          </span>
        </span>
      ),
    },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status || 'draft'} /> },
    {
      key: 'active', header: 'Active',
      render: (r) => r.status === 'archived'
        ? <span className="text-muted-foreground">—</span>
        : (
          <Switch
            checked={r.status === 'active'}
            disabled={togglingId === r.id}
            label={`${r.status === 'active' ? 'Deactivate' : 'Activate'} ${r.name}`}
            onCheckedChange={(v) => onToggleActive(r, v)}
          />
        ),
    },
    {
      key: 'versions', header: 'Versions',
      render: (r) => <span className="tabular-nums text-muted-foreground">{r.version_count}</span>,
    },
    {
      key: 'updated', header: 'Updated',
      render: (r) => <span className="text-muted-foreground">{r.updated_at ? new Date(r.updated_at).toLocaleDateString() : '—'}</span>,
    },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (r) => (
        <span className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Link href={`/workflows/${r.id}/edit`} aria-label={`Edit ${r.name}`} className="rounded-md p-2 hover:bg-accent">
            <Pencil className="h-4 w-4" />
          </Link>
          <PermissionGate permission="workflow:create">
            <button aria-label={`Duplicate ${r.name}`} className="rounded-md p-2 hover:bg-accent" onClick={() => onDuplicate(r)}>
              <Copy className="h-4 w-4" />
            </button>
          </PermissionGate>
          <PermissionGate permission="workflow:delete">
            <button aria-label={`Delete ${r.name}`} className="rounded-md p-2 text-destructive hover:bg-destructive/10" onClick={() => setDeleting(r)}>
              <Trash2 className="h-4 w-4" />
            </button>
          </PermissionGate>
        </span>
      ),
    },
  ];

  return (
    <Protected>
      <Layout>
        <div className="oa-page">
          <PageHeader
            title="Workflows"
            description="Author multi-step automations in the visual builder, then test-run the canvas locally."
            breadcrumbs={[{ label: 'Home', href: '/' }, { label: 'Workflows' }]}
            actions={
              <PermissionGate permission="workflow:create">
                <div className="flex items-center gap-2">
                  <Button variant="outline" onClick={() => setGenerateOpen(true)}>
                    <Sparkles className="mr-2 h-4 w-4 text-primary" />
                    Generate with AI
                  </Button>
                  <Button onClick={() => setCreateOpen(true)}><Plus className="mr-2 h-4 w-4" />New workflow</Button>
                </div>
              </PermissionGate>
            }
          />
          <div className="flex flex-col gap-2 rounded-xl border border-border/70 bg-card p-2.5 shadow-card sm:flex-row">
            <div className="relative w-full max-w-sm">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search workflows…" aria-label="Search workflows" className="pl-9" />
            </div>
            <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status" className="rounded-lg border-transparent bg-muted/60 sm:w-44">
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
            </Select>
            <span className="ml-auto hidden items-center gap-1.5 self-center text-xs text-muted-foreground md:flex">
              <kbd className="oa-kbd">Ctrl K</kbd> to jump to anything
            </span>
          </div>
          <DataTable
            columns={columns}
            rows={items}
            keyOf={(r) => r.id}
            loading={isLoading}
            error={error}
            onRetry={() => refetch()}
            emptyTitle="No workflows yet"
            emptyDescription="Create your first workflow to automate your AI workforce — start blank or from a template."
            emptyAction={
              <PermissionGate permission="workflow:create">
                <Button onClick={() => setCreateOpen(true)}><Plus className="mr-2 h-4 w-4" />New workflow</Button>
              </PermissionGate>
            }
          />
          <p className="oa-caption">{total} workflow{total === 1 ? '' : 's'}</p>
        </div>
      </Layout>

      <CreateWorkflowDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <GenerateWithAiDialog open={generateOpen} onClose={() => setGenerateOpen(false)} />

      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={onDelete}
        title={`Delete “${deleting?.name}”?`}
        description="The workflow and its versions will be archived from this list. Past executions are preserved."
        confirmLabel="Delete"
        danger
      />
    </Protected>
  );
}
