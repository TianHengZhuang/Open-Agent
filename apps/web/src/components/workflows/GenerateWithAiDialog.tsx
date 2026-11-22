'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { toUserMessage } from '@/lib/api';
import { useKoroChat } from '@/features/koro/koro-api';

const EXAMPLES = [
  'When a new support email arrives, classify it, draft a reply and post urgent ones to Slack',
  'Every Monday at 9am, summarize last week’s invoices and email the totals to finance',
  'When a lead fills the form, enrich the company and notify sales in Slack',
  'Build a workflow that checks a form submission, flags missing fields and logs the result',
];

export function GenerateWithAiDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [prompt, setPrompt] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);
  const chat = useKoroChat();
  const router = useRouter();

  const close = () => {
    if (chat.isPending) return;
    setPrompt('');
    setError(null);
    onClose();
  };

  const submit = async (event?: React.FormEvent) => {
    event?.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed || chat.isPending) return;
    setError(null);
    try {
      const result = await chat.mutateAsync({ message: trimmed, preview: false });
      if (result.workflow_id) {
        setPrompt('');
        onClose();
        router.push(`/workflows/${result.workflow_id}/edit`);
        return;
      }
      const action = result.actions[0];
      setError(
        action?.status === 'failed'
          ? action.detail
          : result.reply ||
              'No workflow was created. Describe the trigger and the steps you want.',
      );
    } catch (err) {
      setError(toUserMessage(err));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Generate a workflow with AI"
      description="Describe the automation in plain language. It is created as a draft you can review and test before publishing."
      wide
      footer={
        <>
          <Button variant="outline" onClick={close} disabled={chat.isPending}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={chat.isPending}>
            <Sparkles className="mr-1.5 h-4 w-4" />
            Generate workflow
          </Button>
        </>
      }
    >
      <form className="space-y-3" onSubmit={submit}>
        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <label className="block space-y-1">
          <span className="text-sm font-medium">What should it do?</span>
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={4}
            autoFocus
            placeholder="When a new invoice is received, extract the total and vendor, then flag anything over $5,000 for approval."
            className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </label>
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Try an example</p>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setPrompt(example)}
                className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-left text-[11px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
              >
                {example}
              </button>
            ))}
          </div>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Works with a local model or, without one, from deterministic command parsing.
          You can change every node afterwards.
        </p>
      </form>
    </Dialog>
  );
}
