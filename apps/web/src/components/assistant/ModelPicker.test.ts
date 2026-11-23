import { describe, expect, it } from 'vitest';
import { API_PROVIDERS } from './ModelPicker';

describe('API key providers', () => {
  it('covers the requested hosted providers plus a custom endpoint', () => {
    const ids = API_PROVIDERS.map((provider) => provider.id);
    expect(ids).toEqual(
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

  it('gives every provider an OpenAI-compatible base URL except Custom', () => {
    for (const provider of API_PROVIDERS) {
      if (provider.id === 'custom-openai') {
        expect(provider.baseUrl).toBe('');
      } else {
        expect(provider.baseUrl).toMatch(/^https:\/\//);
      }
      expect(provider.hint.length).toBeGreaterThan(0);
    }
  });

  it('sends Anthropic through its OpenAI-compatible endpoint', () => {
    const anthropic = API_PROVIDERS.find((provider) => provider.id === 'anthropic');
    expect(anthropic?.baseUrl).toBe('https://api.anthropic.com/v1');
  });
});
