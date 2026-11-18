'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useOrganization } from '@/context/OrganizationContext';

export interface KoroStatus {
  ready: boolean;
  provider: string;
  model: string;
  model_size: string;
  base_url: string;
  models: string[];
  install_hint: string;
}

export interface KoroAction {
  type: string;
  status: 'ok' | 'failed';
  detail: string;
  workflow_id?: string | null;
  data?: Record<string, unknown> | null;
}

export interface KoroChatResult {
  reply: string;
  source: 'model' | 'commands';
  actions: KoroAction[];
  workflow_id?: string | null;
}

export interface KoroMessage {
  role: 'user' | 'assistant';
  content: string;
}

function base(orgId: string | null): string {
  return `/organizations/${orgId}/copilot`;
}

export function useKoroStatus(enabled = true) {
  const { currentOrgId } = useOrganization();
  return useQuery({
    queryKey: ['koro', 'status', currentOrgId],
    queryFn: () => api.get<KoroStatus>(`${base(currentOrgId)}/status`),
    enabled: enabled && !!currentOrgId,
    staleTime: 20_000,
    retry: false,
  });
}

export function useKoroChat() {
  const { currentOrgId } = useOrganization();
  return useMutation({
    mutationFn: (input: {
      message: string;
      workflow_id?: string;
      history?: KoroMessage[];
      input?: Record<string, unknown>;
    }) => api.post<KoroChatResult>(`${base(currentOrgId)}/chat`, input),
  });
}
