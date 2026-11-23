import { describe, expect, it } from 'vitest';
import {
  API_PROVIDERS,
  LOCAL_PROVIDERS,
  MODEL_PROVIDERS,
  MODEL_PREFS_KEY,
  capabilitiesFor,
  filterModelOptions,
  isLocalBaseUrl,
  loadModelPreferences,
  makeModelOption,
  modelKey,
  pillLabel,
  providerForBaseUrl,
  rememberModel,
  resolveOptions,
  toggleModelFavorite,
} from './ai-models';

const options = [
  makeModelOption({ model: 'qwen2.5-coder:7b', baseUrl: 'http://127.0.0.1:11434/v1' }),
  makeModelOption({ model: 'llama3.2:3b', baseUrl: 'http://127.0.0.1:11434/v1' }),
  makeModelOption({ model: 'claude-3-5-sonnet', baseUrl: 'https://api.anthropic.com/v1' }),
  makeModelOption({ model: 'gpt-4o-mini', baseUrl: 'https://api.openai.com/v1' }),
];

function memoryStorage(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    dump: () => Object.fromEntries(store),
  };
}

describe('model registry', () => {
  it('lists local and API-key providers with their transport', () => {
    expect(MODEL_PROVIDERS).toHaveLength(LOCAL_PROVIDERS.length + API_PROVIDERS.length);
    for (const provider of LOCAL_PROVIDERS) {
      expect(provider.source).toBe('local');
      expect(provider.keyRequired).toBe(false);
    }
    for (const provider of API_PROVIDERS) {
      expect(provider.source).toBe('api-key');
      expect(provider.keyRequired).toBe(true);
    }
    expect(LOCAL_PROVIDERS.map((p) => p.id)).toEqual(
      expect.arrayContaining(['ollama', 'lmstudio', 'localai', 'custom-local']),
    );
    expect(API_PROVIDERS.map((p) => p.id)).toEqual(
      expect.arrayContaining([
        'openai',
        'anthropic',
        'gemini',
        'openrouter',
        'groq',
        'custom-openai',
      ]),
    );
  });

  it('resolves a provider from the endpoint', () => {
    expect(providerForBaseUrl('http://localhost:11434').id).toBe('ollama');
    expect(providerForBaseUrl('http://127.0.0.1:11434/v1/').id).toBe('ollama');
    expect(providerForBaseUrl('http://localhost:1234/v1').id).toBe('lmstudio');
    expect(providerForBaseUrl('http://127.0.0.1:8080/v1').id).toBe('custom-local');
    expect(providerForBaseUrl('https://api.openai.com/v1').id).toBe('openai');
    expect(providerForBaseUrl('https://api.anthropic.com/v1').id).toBe('anthropic');
    expect(providerForBaseUrl('https://openrouter.ai/api/v1').id).toBe('openrouter');
    expect(providerForBaseUrl('https://llm.example.com/v1').id).toBe('custom-openai');
    expect(providerForBaseUrl('').id).toBe('custom-openai');
  });

  it('detects loopback endpoints', () => {
    expect(isLocalBaseUrl('http://localhost:11434/v1')).toBe(true);
    expect(isLocalBaseUrl('http://127.0.0.1:1234/v1')).toBe(true);
    expect(isLocalBaseUrl('https://api.openai.com/v1')).toBe(false);
  });
});

describe('capabilities', () => {
  it('marks local models and infers code/vision/tools', () => {
    expect(capabilitiesFor('qwen2.5-coder:7b', 'local')).toEqual(
      expect.arrayContaining(['local', 'code', 'tools']),
    );
    expect(capabilitiesFor('llava:13b', 'local')).toContain('vision');
    expect(capabilitiesFor('gpt-4o-mini', 'api-key')).toEqual(
      expect.arrayContaining(['vision', 'tools']),
    );
    expect(capabilitiesFor('gpt-4o-mini', 'api-key')).not.toContain('local');
    expect(capabilitiesFor('llama3.2:3b', 'local')).toEqual(['local', 'tools']);
  });
});

describe('picker helpers', () => {
  it('searches by model and provider name', () => {
    expect(filterModelOptions(options, 'coder').map((option) => option.model)).toEqual([
      'qwen2.5-coder:7b',
    ]);
    expect(filterModelOptions(options, 'anthropic').map((option) => option.model)).toEqual([
      'claude-3-5-sonnet',
    ]);
    expect(filterModelOptions(options, '  ')).toHaveLength(4);
  });

  it('labels the composer pill with provider and model', () => {
    expect(pillLabel('Ollama', 'qwen2.5-coder:7b')).toBe('Ollama · qwen2.5-coder:7b');
    expect(pillLabel('', 'llama3.2:3b')).toBe('llama3.2:3b');
    expect(pillLabel('Ollama', '')).toBe('Choose a model');
  });

  it('builds stable ids per provider and model', () => {
    expect(modelKey('ollama', 'llama3.2:3b')).toBe('ollama::llama3.2:3b');
    expect(options[0].id).toBe('ollama::qwen2.5-coder:7b');
    expect(options[2].source).toBe('api-key');
  });
});

describe('favorites and recents', () => {
  it('toggles favorites and keeps the latest recents', () => {
    const storage = memoryStorage();
    let prefs = loadModelPreferences(storage);
    expect(prefs).toEqual({ favorites: [], recent: [] });

    prefs = toggleModelFavorite('ollama::llama3.2:3b', prefs, storage);
    prefs = toggleModelFavorite('openai::gpt-4o-mini', prefs, storage);
    expect(prefs.favorites).toEqual(['openai::gpt-4o-mini', 'ollama::llama3.2:3b']);

    prefs = toggleModelFavorite('openai::gpt-4o-mini', prefs, storage);
    expect(prefs.favorites).toEqual(['ollama::llama3.2:3b']);

    prefs = rememberModel('ollama::llama3.2:3b', prefs, storage);
    prefs = rememberModel('openai::gpt-4o-mini', prefs, storage);
    expect(prefs.recent).toEqual(['openai::gpt-4o-mini', 'ollama::llama3.2:3b']);

    prefs = rememberModel('ollama::llama3.2:3b', prefs, storage);
    expect(prefs.recent).toEqual(['ollama::llama3.2:3b', 'openai::gpt-4o-mini']);
    expect(Object.keys(storage.dump())).toEqual([MODEL_PREFS_KEY]);
  });

  it('survives corrupt storage and resolves only available ids', () => {
    expect(loadModelPreferences(memoryStorage({ [MODEL_PREFS_KEY]: 'nope' }))).toEqual({
      favorites: [],
      recent: [],
    });
    expect(resolveOptions(['ollama::llama3.2:3b', 'gone'], options).map((o) => o.model)).toEqual([
      'llama3.2:3b',
    ]);
  });
});
