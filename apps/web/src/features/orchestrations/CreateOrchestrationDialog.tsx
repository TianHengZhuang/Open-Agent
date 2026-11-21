'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field, Select, Textarea } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { useToast } from '@/components/ui/toast';
import { toUserMessage } from '@/lib/api';
import { useCreateOrchestration, usePlanOrchestration } from './api';

const STRATEGIES = [
  { value: 'auto', label: 'Auto-plan', hint: 'Rule-based planner decomposes the objective into executable tasks.' },
  { value: 'research_team', label: 'Research team', hint: 'Parallel web + market research, followed by consolidated analysis.' },
  { value: 'software_team', label: 'Software team', hint: 'Backend + frontend, automated testing, then security review.' },
  { value: 'content_team', label: 'Content team', hint: 'Research → draft → SEO pass → editorial review.' },
] as const;

const BUDGET_FIELDS = [
  { key: 'max_tasks', label: 'Max tasks', min: 1, step: 1 },
  { key: 'max_agents', label: 'Max agents', min: 1, step: 1 },
  { key: 'max_parallel_tasks', label: 'Parallel tasks', min: 1, step: 1 },
  { key: 'max_total_steps', label: 'Max steps', min: 1, step: 1 },
  { key: 'max_total_tokens', label: 'Token budget', min: 1, step: 1000 },
  { key: 'max_total_cost', label: 'Cost budget (USD)', min: 0.01, step: 0.5 },
  { key: 'max_execution_time_seconds', label: 'Time budget (seconds)', min: 1, step: 60 },
  { key: 'max_delegation_depth', label: 'Max delegation depth', min: 1, step: 1 },
] as const;

const DEFAULT_BUDGET: Record<string, string> = {
  max_tasks: '200',
  max_agents: '50',
  max_parallel_tasks: '10',
  max_total_steps: '1000',
  max_total_tokens: '500000',
  max_total_cost: '10',
  max_execution_time_seconds: '3600',
  max_delegation_depth: '5',
};

export function CreateOrchestrationDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const { toast } = useToast();
  const create = useCreateOrchestration();
  const plan = usePlanOrchestration();
  const [objective, setObjective] = React.useState('');
  const [strategy, setStrategy] = React.useState('auto');
  const [budget, setBudget] = React.useState<Record<string, string>>(DEFAULT_BUDGET);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setObjective('');
    setStrategy('auto');
    setBudget(DEFAULT_BUDGET);
    setError(null);
  }, [open]);

  const selectedStrategy = STRATEGIES.find((s) => s.value === strategy) ?? STRATEGIES[0];
  const pending = create.isPending || plan.isPending;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const trimmed = objective.trim();
    if (!trimmed) {
      setError('Describe the objective this team should accomplish.');
      return;
    }

    const parsedBudget: Record<string, number> = {};
    for (const field of BUDGET_FIELDS) {
      const value = Number(budget[field.key]);
      if (!Number.isFinite(value) || value < field.min) {
        setError(`${field.label} must be at least ${field.min}.`);
        return;
      }
      parsedBudget[field.key] = value;
    }

    let run;
    try {
      run = await create.mutateAsync({
        objective: trimmed,
        budget: parsedBudget,
        ...(strategy === 'auto' ? {} : { template: strategy }),
      });
    } catch (err) {
      setError(toUserMessage(err));
      return;
    }

    let planError: string | null = null;
    if (strategy === 'auto') {
      try {
        await plan.mutateAsync(run.id);
      } catch (err) {
        planError = toUserMessage(err);
      }
    }

    onClose();
    toast({
      kind: planError ? 'warning' : 'success',
      title: planError ? 'Run created, planning needs attention' : 'Orchestration planned',
      description: planError ?? 'Review the task graph, then start the team when ready.',
    });
    router.push(`/orchestrations/${run.id}`);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New orchestration"
      description="Define the objective, choose a team strategy, and set hard execution limits."
      wide
      footer={
        <>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={pending} disabled={!objective.trim()}>
            <Sparkles className="mr-2 h-4 w-4" />
            {pending ? 'Planning…' : 'Create & plan'}
          </Button>
        </>
      }
    >
      <form className="space-y-5" onSubmit={submit}>
        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</p>
        )}
        <Field label="Objective" htmlFor="orch-objective" required help="Be specific about the deliverable and constraints.">
          <Textarea
            id="orch-objective"
            value={objective}
            onChange={(e) => { setObjective(e.target.value); setError(null); }}
            placeholder="e.g. Research the AI automation market, compare the top five competitors, and prepare an investor-ready SaaS proposal."
            rows={4}
            autoFocus
          />
        </Field>
        <Field label="Team strategy" htmlFor="orch-strategy" help={selectedStrategy.hint}>
          <Select id="orch-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {STRATEGIES.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
        </Field>
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <h3 className="text-sm font-medium">Execution guardrails</h3>
            <span className="text-xs text-muted-foreground">Hard limits enforced by the runtime</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {BUDGET_FIELDS.map((field) => (
              <Field key={field.key} label={field.label} htmlFor={`orch-${field.key}`}>
                <Input
                  id={`orch-${field.key}`}
                  type="number"
                  min={field.min}
                  step={field.step}
                  value={budget[field.key]}
                  onChange={(e) => setBudget((prev) => ({ ...prev, [field.key]: e.target.value }))}
                />
              </Field>
            ))}
          </div>
        </div>
      </form>
    </Dialog>
  );
}