// `@` mention resources assembled from everything the assistant can see:
// workflows, the open canvas, upstream data, credentials, templates and saved
// prompts. Pure helpers so the picker stays testable without a DOM.

import type { MentionResource } from './mentions';

export interface SavedPrompt {
  id: string;
  text: string;
}

export const SAVED_PROMPTS_KEY = 'oa:assistant-saved-prompts';
export const MAX_SAVED_PROMPTS = 20;

function promptId(text: string): string {
  return `p_${text.trim().toLowerCase().replace(/\s+/g, '-').slice(0, 80)}`;
}

/** Saved prompts from local storage; corrupt or absent values yield []. */
export function loadSavedPrompts(storage?: Pick<Storage, 'getItem'>): SavedPrompt[] {
  const store = storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined);
  if (!store) return [];
  try {
    const raw = store.getItem(SAVED_PROMPTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is SavedPrompt =>
          !!item &&
          typeof item === 'object' &&
          typeof (item as SavedPrompt).text === 'string' &&
          (item as SavedPrompt).text.trim().length > 0,
      )
      .slice(0, MAX_SAVED_PROMPTS);
  } catch {
    return [];
  }
}

/** Store a prompt once (most recent first, capped) and return the new list. */
export function savePrompt(
  text: string,
  existing: SavedPrompt[],
  storage?: Pick<Storage, 'setItem'>,
): SavedPrompt[] {
  const value = text.trim();
  if (!value) return existing;
  const next: SavedPrompt[] = [
    { id: promptId(value), text: value },
    ...existing.filter((item) => item.text.trim().toLowerCase() !== value.toLowerCase()),
  ].slice(0, MAX_SAVED_PROMPTS);
  const store = storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined);
  if (store) {
    try {
      store.setItem(SAVED_PROMPTS_KEY, JSON.stringify(next));
    } catch {
      // Quota — prompts stay in memory for this session.
    }
  }
  return next;
}

export interface MentionSource {
  workflows?: { id: string; name: string; status?: string }[];
  nodes?: { id: string; name: string; type?: string }[];
  credentials?: { id: string; name: string; provider?: string | null }[];
  variables?: { name: string; type?: string }[];
  templates?: { id: string; name: string; detail?: string }[];
  /** The open canvas has data an expression could read. */
  hasUpstream?: boolean;
  prompts?: string[];
  savedPrompts?: SavedPrompt[];
}

/**
 * Build the picker's resource list. Ordering follows intent: what you are
 * editing, then data, then reusable pieces, then prompt starters.
 */
export function buildMentionResources(source: MentionSource): MentionResource[] {
  const resources: MentionResource[] = [];
  const seen = new Set<string>();
  const push = (resource: MentionResource) => {
    const key = `${resource.kind}:${resource.id}`;
    if (seen.has(key)) return;
    seen.add(key);
    resources.push(resource);
  };

  for (const workflow of source.workflows ?? []) {
    push({ kind: 'workflow', id: workflow.id, label: workflow.name, detail: workflow.status });
  }
  for (const node of source.nodes ?? []) {
    push({ kind: 'node', id: node.id, label: node.name, detail: node.type });
  }
  if (source.hasUpstream) {
    push({
      kind: 'json',
      id: '$json',
      label: 'Upstream data',
      detail: 'Insert {{ $json }} into the prompt',
      insert: '{{ $json }}',
    });
  }
  for (const credential of source.credentials ?? []) {
    push({
      kind: 'credential',
      id: credential.id,
      label: credential.name,
      detail: credential.provider ?? 'credential',
    });
  }
  for (const variable of source.variables ?? []) {
    push({
      kind: 'vars',
      id: variable.name,
      label: variable.name,
      detail: variable.type ?? 'variable',
      insert: `{{ $vars.${variable.name} }}`,
    });
  }
  for (const template of source.templates ?? []) {
    push({ kind: 'template', id: template.id, label: template.name, detail: template.detail });
  }
  for (const saved of source.savedPrompts ?? []) {
    push({ kind: 'prompt', id: saved.id, label: saved.text.slice(0, 70), insert: saved.text });
  }
  for (const prompt of source.prompts ?? []) {
    push({ kind: 'snippet', id: prompt, label: prompt, insert: prompt });
  }

  return resources;
}
