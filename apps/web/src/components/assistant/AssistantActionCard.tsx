'use client';

import * as React from 'react';
import Link from 'next/link';
import { Check, ChevronDown, GitBranch, Play, X } from 'lucide-react';
import { cn } from '@/lib/utils';

/** One action stored on, or returned for, an assistant message. */
export type DisplayAction = {
  type: string;
  status: string;
  detail: string;
  workflow_id?: string | null;
};

/** Where an assistant action should take the user next. */
export function actionTarget(action: DisplayAction): { href: string; label: string } | null {
  if (action.type === 'build' || action.type === 'edit') {
    return action.workflow_id
      ? { href: `/workflows/${action.workflow_id}/edit`, label: 'Open in builder' }
      : null;
  }
  if (action.type === 'create_agent') return { href: '/agents', label: 'Open agents' };
  if (action.type === 'run' || action.type === 'inspect' || action.type === 'diagnose') {
    return { href: '/runs', label: 'View runs' };
  }
  if (action.type === 'publish' || action.type === 'unpublish') {
    return action.workflow_id
      ? { href: `/workflows/${action.workflow_id}`, label: 'View workflow' }
      : null;
  }
  return null;
}

/**
 * Collapsible "Steps / Tool actions" summary for one assistant turn. The
 * actions the copilot took (build, edit, run, diagnose…) stay tucked away
 * until the user asks for them.
 */
export function AssistantStepsCard({ actions }: { actions: DisplayAction[] }) {
  const [open, setOpen] = React.useState(true);
  const visible = actions.filter((action) => action.type !== 'none');
  if (visible.length === 0) return null;
  const failed = visible.some((action) => action.status !== 'ok');

  return (
    <div className="w-full rounded-lg border border-border/70 bg-card/60">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[11px] font-medium text-muted-foreground hover:bg-accent/60 hover:text-foreground"
      >
        <ChevronDown
          className={cn('h-3.5 w-3.5 shrink-0 transition-transform', !open && '-rotate-90')}
          aria-hidden
        />
        Steps / Tool actions
        <span className="rounded-full border border-border/70 px-1.5 text-[10px]">
          {visible.length}
        </span>
        <span
          className={cn(
            'ml-auto h-1.5 w-1.5 rounded-full',
            failed ? 'bg-destructive' : 'bg-emerald-500',
          )}
          aria-hidden
        />
        <span className="text-[10px] uppercase tracking-wide">
          {failed ? 'Needs attention' : 'Done'}
        </span>
      </button>
      {open && (
        <ul className="space-y-1 border-t border-border/60 p-2">
          {visible.map((action, index) => (
            <li key={`${action.type}-${index}`}>
              <AssistantActionCard action={action} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AssistantActionCard({ action }: { action: DisplayAction }) {
  if (action.type === 'none') return null;
  const target = actionTarget(action);
  const failed = action.status !== 'ok';
  return (
    <div
      className={cn(
        'rounded-lg border px-3 py-2 text-xs',
        failed
          ? 'border-destructive/30 bg-destructive/[0.06] text-destructive'
          : 'border-emerald-500/25 bg-emerald-500/[0.06] text-emerald-700 dark:text-emerald-300',
      )}
    >
      <p className="flex items-start gap-2">
        {failed ? (
          <X className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        ) : (
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        )}
        <span className="min-w-0 flex-1 leading-5">{action.detail}</span>
      </p>
      {target && !failed && (
        <Link
          href={target.href}
          className="ml-5 mt-1.5 inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium underline-offset-2 hover:underline"
        >
          {action.type === 'run' || action.type === 'diagnose' ? (
            <Play className="h-3 w-3" aria-hidden />
          ) : (
            <GitBranch className="h-3 w-3" aria-hidden />
          )}
          {target.label}
        </Link>
      )}
    </div>
  );
}
