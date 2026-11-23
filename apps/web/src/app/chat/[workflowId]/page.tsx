'use client';

// Hosted chat page: the shareable surface for a workflow with a Chat Trigger.
// It runs the published version; sessions stay in this browser.

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, MessagesSquare, ShieldCheck } from 'lucide-react';
import { Layout } from '@/components/layout';
import { Protected } from '@/components/protected';
import { Button } from '@/components/ui/button';
import { PageLoading } from '@/components/ui/loading';
import { EmptyState, ErrorState } from '@/components/ui/states';
import { OpenAgentChatSystem } from '@/components/chat/OpenAgentChatSystem';
import { useWorkflow } from '@/features/workflows/workflows-api';

function asText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function asList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === 'string' && !!item.trim());
  return items.length > 0 ? items : undefined;
}

export default function HostedChatPage() {
  const params = useParams<{ workflowId: string }>();
  const workflowId = params.workflowId;
  const router = useRouter();
  const { workflow, isLoading, error, refetch } = useWorkflow(workflowId);

  const trigger = workflow?.latest_version?.definition.triggers.find((t) => t.type === 'chat');
  const config = (trigger?.config ?? {}) as Record<string, unknown>;
  const title = asText(config.title) ?? workflow?.name ?? 'Workflow chat';
  const welcome = asText(config.welcome_message);
  const starters = asList(config.starter_prompts);

  return (
    <Protected>
      <Layout>
        <div className="flex h-full min-h-0 flex-col bg-background">
          <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card px-3">
            {workflow ? (
              <Link
                href={`/workflows/${workflow.id}/edit`}
                className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
                Studio
              </Link>
            ) : null}
            <div className="flex min-w-0 items-center gap-2">
              <MessagesSquare className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <span className="truncate text-sm font-semibold">{title}</span>
              {workflow?.latest_version && (
                <span className="oa-caption shrink-0">v{workflow.latest_version.version}</span>
              )}
            </div>
            <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
              Local-first
            </span>
          </header>

          <main className="min-h-0 flex-1">
            {error ? (
              <ErrorState description={error} onRetry={() => refetch()} />
            ) : isLoading && !workflow ? (
              <PageLoading label="Loading chat…" />
            ) : !workflow ? (
              <EmptyState
                icon={MessagesSquare}
                title="Workflow not found"
                description="This chat page points at a workflow you cannot open in the current organization."
                action={
                  <Button variant="outline" onClick={() => router.push('/workflows')}>
                    Back to workflows
                  </Button>
                }
              />
            ) : !trigger ? (
              <EmptyState
                icon={MessagesSquare}
                title="No Chat Trigger yet"
                description="Add a Chat Trigger to this workflow and save a version — its title, welcome message and starter prompts appear here."
                action={
                  <Button
                    variant="outline"
                    onClick={() => router.push(`/workflows/${workflow.id}/edit`)}
                  >
                    Open the studio
                  </Button>
                }
              />
            ) : (
              <OpenAgentChatSystem
                key={workflow.latest_version?.id ?? workflow.id}
                variant="full"
                workflowId={workflow.id}
                workflowName={workflow.name}
                title={title}
                welcome={welcome}
                starters={starters}
                disabled={workflow.status === 'archived'}
                onInspectNode={(nodeId) =>
                  router.push(`/workflows/${workflow.id}/edit?node=${encodeURIComponent(nodeId)}`)
                }
              />
            )}
          </main>
        </div>
      </Layout>
    </Protected>
  );
}
