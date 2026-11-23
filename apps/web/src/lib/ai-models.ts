// One registry for every model OpenAgent can talk to: local runtimes and
// API-key providers. Pure data + helpers, so the picker, the chat pill and the
// tests agree on names, capabilities and persistence keys.

export type ModelSource = 'local' | 'api-key';

export type ProviderId =
  | 'ollama'
  | 'lmstudio'
  | 'localai'
  | 'custom-local'
  | 'openai'
  | 'anthropic'
  | 'gemini'
  | 'openrouter'
  | 'groq'
  | 'mistral'
  | 'deepseek'
  | 'custom-openai';

export interface ModelProvider {
  id: ProviderId;
  name: string;
  source: ModelSource;
  /** Default OpenAI-compatible base URL (empty for user-supplied endpoints). */
  baseUrl: string;
  /** Short description shown under the provider chip. */
  hint: string;
  /** Where a key comes from; local runtimes need none. */
  keyRequired: boolean;
}

const LOCAL_HOSTS = ['127.0.0.1', 'localhost', '::1', '0.0.0.0'];

export const MODEL_PROVIDERS: ModelProvider[] = [
  {
    id: 'ollama',
    name: 'Ollama',
    source: 'local',
    baseUrl: 'http://127.0.0.1:11434/v1',
    hint: 'Local models served by Ollama',
    keyRequired: false,
  },
  {
    id: 'lmstudio',
    name: 'LM Studio',
    source: 'local',
    baseUrl: 'http://127.0.0.1:1234/v1',
    hint: 'Local models served by LM Studio',
    keyRequired: false,
  },
  {
    id: 'localai',
    name: 'LocalAI',
    source: 'local',
    baseUrl: 'http://127.0.0.1:8080/v1',
    hint: 'Self-hosted LocalAI server',
    keyRequired: false,
  },
  {
    id: 'custom-local',
    name: 'Custom local',
    source: 'local',
    baseUrl: '',
    hint: 'Any OpenAI-compatible endpoint on this machine',
    keyRequired: false,
  },
  {
    id: 'openai',
    name: 'OpenAI',
    source: 'api-key',
    baseUrl: 'https://api.openai.com/v1',
    hint: 'gpt-4o-mini, gpt-4.1',
    keyRequired: true,
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    source: 'api-key',
    baseUrl: 'https://api.anthropic.com/v1',
    hint: 'claude-3-5-sonnet-latest (OpenAI-compatible endpoint)',
    keyRequired: true,
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    source: 'api-key',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    hint: 'gemini-2.0-flash',
    keyRequired: true,
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    source: 'api-key',
    baseUrl: 'https://openrouter.ai/api/v1',
    hint: 'Any hosted model behind one key',
    keyRequired: true,
  },
  {
    id: 'groq',
    name: 'Groq',
    source: 'api-key',
    baseUrl: 'https://api.groq.com/openai/v1',
    hint: 'llama-3.3-70b-versatile',
    keyRequired: true,
  },
  {
    id: 'mistral',
    name: 'Mistral',
    source: 'api-key',
    baseUrl: 'https://api.mistral.ai/v1',
    hint: 'mistral-small-latest',
    keyRequired: true,
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    source: 'api-key',
    baseUrl: 'https://api.deepseek.com/v1',
    hint: 'deepseek-chat',
    keyRequired: true,
  },
  {
    id: 'custom-openai',
    name: 'Custom',
    source: 'api-key',
    baseUrl: '',
    hint: 'Any OpenAI-compatible base URL',
    keyRequired: true,
  },
];

export const LOCAL_PROVIDERS = MODEL_PROVIDERS.filter((p) => p.source === 'local');
export const API_PROVIDERS = MODEL_PROVIDERS.filter((p) => p.source === 'api-key');

function normalize(url: string): string {
  return url.trim().replace(/\/+$/, '').toLowerCase();
}

export function isLocalBaseUrl(url: string): boolean {
  const host = normalize(url).replace(/^https?:\/\//, '').split('/')[0].split(':')[0];
  return LOCAL_HOSTS.includes(host) || host.endsWith('.local');
}

/** Which provider a base URL belongs to (falls back to a custom endpoint). */
export function providerForBaseUrl(baseUrl: string): ModelProvider {
  const url = normalize(baseUrl);
  const local = isLocalBaseUrl(url);
  if (!url) return local ? providerById('custom-local') : providerById('custom-openai');
  if (url.includes('11434') || url.includes('ollama')) return providerById('ollama');
  if (url.includes(':1234') || url.includes('lmstudio')) return providerById('lmstudio');
  if (url.includes('localai')) return providerById('localai');
  if (local) return providerById('custom-local');
  for (const provider of API_PROVIDERS) {
    if (provider.baseUrl && url.startsWith(normalize(provider.baseUrl))) return provider;
  }
  return providerById('custom-openai');
}

export function providerById(id: ProviderId): ModelProvider {
  return MODEL_PROVIDERS.find((provider) => provider.id === id) ?? MODEL_PROVIDERS[0];
}

/** `Ollama · qwen2.5-coder:7b` — used by the composer pill. */
export function pillLabel(providerName: string, model: string): string {
  const name = providerName.trim();
  const value = model.trim();
  if (!value) return 'Choose a model';
  if (!name) return value;
  return `${name} · ${value}`;
}

export type Capability = 'local' | 'tools' | 'vision' | 'code';

const CODE_PATTERN = /(coder|codex|starcoder|code-?llama|deepseek-coder|devstral)/i;
const VISION_PATTERN = /(vision|llava|pixtral|gemma3|gpt-4o|gpt-4\.1|claude|gemini|qwen2\.5-vl|vl-)/i;
const TOOLS_PATTERN =
  /(gpt-4|gpt-3\.5-turbo|claude|gemini|llama-?3\.[123]|qwen|mistral|mixtral|command-r|deepseek-chat)/i;

/** Best-effort capability badges from the model name and where it runs. */
export function capabilitiesFor(
  model: string,
  source: ModelSource = 'local',
): Capability[] {
  const name = model.toLowerCase();
  const caps: Capability[] = [];
  if (source === 'local' || /[:\-_]local$/.test(name)) caps.push('local');
  if (TOOLS_PATTERN.test(name)) caps.push('tools');
  if (VISION_PATTERN.test(name)) caps.push('vision');
  if (CODE_PATTERN.test(name)) caps.push('code');
  return caps;
}

export interface ModelOption {
  /** Stable id used by favorites and recents. */
  id: string;
  providerId: ProviderId;
  providerName: string;
  model: string;
  baseUrl: string;
  source: ModelSource;
  capabilities: Capability[];
}

export function modelKey(providerId: ProviderId, model: string): string {
  return `${providerId}::${model}`;
}

export function makeModelOption(input: {
  model: string;
  baseUrl: string;
  providerId?: ProviderId;
  providerName?: string;
}): ModelOption {
  const provider = input.providerId
    ? providerById(input.providerId)
    : providerForBaseUrl(input.baseUrl);
  return {
    id: modelKey(provider.id, input.model),
    providerId: provider.id,
    providerName: input.providerName ?? provider.name,
    model: input.model,
    baseUrl: input.baseUrl,
    source: provider.source,
    capabilities: capabilitiesFor(input.model, provider.source),
  };
}

/** Search matches the model name first, then the provider. */
export function filterModelOptions(options: ModelOption[], query: string): ModelOption[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return options;
  return options.filter(
    (option) =>
      option.model.toLowerCase().includes(needle) ||
      option.providerName.toLowerCase().includes(needle),
  );
}

export const MODEL_PREFS_KEY = 'oa:model-preferences';
const MAX_RECENT = 6;
const MAX_FAVORITES = 12;

export interface ModelPreferences {
  favorites: string[];
  recent: string[];
}

interface PreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function defaultStorage(): PreferenceStorage | undefined {
  return typeof window !== 'undefined' ? window.localStorage : undefined;
}

export function loadModelPreferences(storage?: PreferenceStorage): ModelPreferences {
  const store = storage ?? defaultStorage();
  if (!store) return { favorites: [], recent: [] };
  try {
    const raw = store.getItem(MODEL_PREFS_KEY);
    if (!raw) return { favorites: [], recent: [] };
    const parsed: unknown = JSON.parse(raw);
    const record = (parsed ?? {}) as Partial<ModelPreferences>;
    const clean = (value: unknown): string[] =>
      Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
    return {
      favorites: clean(record.favorites).slice(0, MAX_FAVORITES),
      recent: clean(record.recent).slice(0, MAX_RECENT),
    };
  } catch {
    return { favorites: [], recent: [] };
  }
}

function persist(prefs: ModelPreferences, storage?: PreferenceStorage): ModelPreferences {
  const store = storage ?? defaultStorage();
  if (store) {
    try {
      store.setItem(MODEL_PREFS_KEY, JSON.stringify(prefs));
    } catch {
      // Quota — preferences stay in memory for this session.
    }
  }
  return prefs;
}

export function toggleModelFavorite(
  id: string,
  prefs: ModelPreferences,
  storage?: PreferenceStorage,
): ModelPreferences {
  const favorites = prefs.favorites.includes(id)
    ? prefs.favorites.filter((item) => item !== id)
    : [id, ...prefs.favorites].slice(0, MAX_FAVORITES);
  return persist({ ...prefs, favorites }, storage);
}

export function rememberModel(
  id: string,
  prefs: ModelPreferences,
  storage?: PreferenceStorage,
): ModelPreferences {
  const recent = [id, ...prefs.recent.filter((item) => item !== id)].slice(0, MAX_RECENT);
  return persist({ ...prefs, recent }, storage);
}

/** Resolve stored ids back to options, dropping ones no longer available. */
export function resolveOptions(ids: string[], options: ModelOption[]): ModelOption[] {
  const byId = new Map(options.map((option) => [option.id, option]));
  return ids
    .map((id) => byId.get(id))
    .filter((option): option is ModelOption => option !== undefined);
}
