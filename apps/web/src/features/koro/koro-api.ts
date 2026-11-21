'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  api_key_set: boolean;
  local: boolean;
  customized: boolean;
}

export interface KoroSettings {
  base_url: string;
  model: string;
  provider: string;
  local: boolean;
  api_key_set: boolean;
  customized: boolean;
  default_base_url: string;
  default_model: string;
  models: string[];
}

export interface KoroSettingsInput {
  base_url: string;
  model: string;
  api_key?: string;
  clear_api_key?: boolean;
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

export function useKoroSettings(enabled = true) {
  const { currentOrgId } = useOrganization();
  return useQuery({
    queryKey: ['koro', 'settings', currentOrgId],
    queryFn: () => api.get<KoroSettings>(`${base(currentOrgId)}/settings`),
    enabled: enabled && !!currentOrgId,
    staleTime: 20_000,
    retry: false,
  });
}

export function useUpdateKoroSettings() {
  const { currentOrgId } = useOrganization();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: KoroSettingsInput) =>
      api.put<KoroSettings>(`${base(currentOrgId)}/settings`, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['koro'] });
    },
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
