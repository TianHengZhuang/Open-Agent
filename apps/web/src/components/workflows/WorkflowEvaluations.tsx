"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowUpRight, ClipboardCheck, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/loading";
import { PermissionGate } from "@/components/PermissionGate";
import { toUserMessage } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useEvaluations } from "@/features/quality/quality-api";
import { decisionTone, type EvaluationRecord } from "@/features/quality/types";

function scoreLabel(score: number | null): string {
  return typeof score === "number" ? `${(score * 100).toFixed(1)}%` : "—";
}

function reasonFor(evaluation: EvaluationRecord): string {
  return (
    evaluation.failure_class ??
    evaluation.failure_reason ??
    evaluation.uncertainty_reason ??
    "—"
  );
}

function EvaluationRow({ evaluation }: { evaluation: EvaluationRecord }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3 text-sm transition-colors hover:bg-accent/40">
      <span
        className={cn(
          "inline-flex shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
          decisionTone(evaluation.decision),
        )}
      >
        {evaluation.decision ?? "PENDING"}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{evaluation.evaluation_type}</p>
        <p className="oa-caption truncate">
          {reasonFor(evaluation)}
          {evaluation.attempt_number > 1
            ? ` · attempt ${evaluation.attempt_number}`
            : ""}
        </p>
      </div>
      <span className="hidden text-xs tabular-nums text-muted-foreground sm:inline">
        {scoreLabel(evaluation.score)}
      </span>
      <StatusBadge status={evaluation.status} />
      <span className="oa-caption hidden w-[132px] text-right lg:inline">
        {evaluation.completed_at
          ? new Date(evaluation.completed_at).toLocaleString()
          : evaluation.created_at
            ? new Date(evaluation.created_at).toLocaleString()
            : "—"}
      </span>
      <Link
        href={`/quality/evaluations/${evaluation.id}`}
        aria-label={`Open evaluation ${evaluation.id}`}
        className="oa-icon-button shrink-0"
      >
        <ArrowUpRight className="h-4 w-4" />
      </Link>
    </li>
  );
}

export function WorkflowEvaluations({ workflowId }: { workflowId: string }) {
  return (
    <PermissionGate
      permission="evaluation:read"
      fallback={
        <div className="flex h-full items-center justify-center p-6">
          <p className="text-sm text-muted-foreground">
            Evaluation view access is required to see this workflow&apos;s
            quality checks.
          </p>
        </div>
      }
    >
      <EvaluationsPanel workflowId={workflowId} />
    </PermissionGate>
  );
}

function EvaluationsPanel({ workflowId }: { workflowId: string }) {
  const { items, total, isLoading, error, refetch } = useEvaluations({
    workflow_id: workflowId,
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <ClipboardCheck
            className="h-4 w-4 text-muted-foreground"
            aria-hidden
          />
          Evaluations
        </h2>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
          {total}
        </span>
        <p className="oa-caption hidden md:block">
          Quality checks recorded against this workflow&apos;s runs.
        </p>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[11px]"
            onClick={() => void refetch()}
          >
            <RefreshCw className="mr-1.5 h-3 w-3" aria-hidden />
            Refresh
          </Button>
          <Link
            href="/quality"
            className="inline-flex h-7 items-center rounded-md px-2 text-[11px] font-medium text-primary hover:bg-accent"
          >
            Quality Center
          </Link>
        </div>
      </div>

      <div className="oa-scroll min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-2 p-3" aria-hidden>
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-14" />
            ))}
          </div>
        ) : error ? (
          <ErrorState
            title="Could not load evaluations"
            description={toUserMessage(error)}
            onRetry={() => void refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            className="h-full"
            icon={ClipboardCheck}
            title="No evaluations for this workflow"
            description="Evaluations appear here when a run is verified — through a quality gate, a verify node, or the Quality Center."
            action={
              <Link href="/quality">
                <Button variant="outline">Open Quality Center</Button>
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border/70">
            {items.map((evaluation) => (
              <EvaluationRow key={evaluation.id} evaluation={evaluation} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
