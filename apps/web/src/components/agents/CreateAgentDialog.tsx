'use client';

import * as React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field, Select } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { useToast } from '@/components/ui/toast';
import { useOrganization } from '@/context/OrganizationContext';
import { api, toUserMessage } from '@/lib/api';

const AGENT_TYPES = [
  { value: 'workflow', label: 'Workflow' },
  { value: 'chat', label: 'Chat' },
  { value: 'assistant', label: 'Assistant' },
  { value: 'autonomous', label: 'Autonomous' },
];

export function CreateAgentDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { currentOrgId } = useOrganization();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [agentType, setAgentType] = React.useState('workflow');
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setName('');
    setDescription('');
    setAgentType('workflow');
    setError(null);
  }, [open]);

  const create = useMutation({
    mutationFn: (body: { name: string; description?: string; agent_type: string }) =>
      api.post(`/organizations/${currentOrgId}/agents`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agents'] }),
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Give your agent a name.');
      return;
    }
    if (!currentOrgId) {
      setError('Select a workspace before creating an agent.');
      return;
    }
    try {
      await create.mutateAsync({
        name: trimmed,
        description: description.trim() || undefined,
        agent_type: agentType,
      });
      toast({ kind: 'success', title: `Created “${trimmed}”`, description: 'The agent is saved as a draft.' });
      onClose();
    } catch (err) {
      setError(toUserMessage(err));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create agent"
      description="Agents start as drafts and can be configured after creation."
      footer={
        <>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={create.isPending} disabled={!name.trim() || !currentOrgId}>
            Create agent
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={submit}>
        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</p>
        )}
        <Field label="Name" htmlFor="agent-name" required>
          <Input id="agent-name" value={name} onChange={(e) => { setName(e.target.value); setError(null); }} placeholder="Research assistant" autoFocus />
        </Field>
        <Field label="Description" htmlFor="agent-description">
          <Input id="agent-description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this agent do?" />
        </Field>
        <Field label="Type" htmlFor="agent-type">
          <Select id="agent-type" value={agentType} onChange={(e) => setAgentType(e.target.value)}>
            {AGENT_TYPES.map((type) => (
              <option key={type.value} value={type.value}>{type.label}</option>
            ))}
          </Select>
        </Field>
      </form>
    </Dialog>
  );
}