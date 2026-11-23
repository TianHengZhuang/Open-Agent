import { describe, expect, it } from 'vitest';
import {
  MAX_SAVED_PROMPTS,
  SAVED_PROMPTS_KEY,
  buildMentionResources,
  loadSavedPrompts,
  savePrompt,
} from './resources';
import { mentionText } from './mentions';

function memoryStorage(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    dump: () => Object.fromEntries(store),
  };
}

describe('mention resources', () => {
  it('builds every resource kind in intent order', () => {
    const resources = buildMentionResources({
      workflows: [{ id: 'wf_1', name: 'Support triage', status: 'ACTIVE' }],
      nodes: [{ id: 'n1', name: 'Normalize', type: 'set' }],
      hasUpstream: true,
      credentials: [{ id: 'c1', name: 'Slack production', provider: 'slack' }],
      templates: [{ id: 't1', name: 'Webhook to Slack', detail: 'template' }],
      savedPrompts: [{ id: 'p1', text: 'Add retries to every HTTP node' }],
      prompts: ['Build a scheduled scraper'],
    });

    expect(resources.map((resource) => resource.kind)).toEqual([
      'workflow',
      'node',
      'json',
      'credential',
      'template',
      'prompt',
      'snippet',
    ]);
    expect(mentionText(resources[2])).toBe('{{ $json }}');
    expect(mentionText(resources[3])).toBe('@Slack production (credential c1)');
    expect(mentionText(resources[4])).toBe('@Webhook to Slack (template t1)');
  });

  it('adds environment variables as $vars expressions', () => {
    const resources = buildMentionResources({
      variables: [{ name: 'slack_channel', type: 'string' }],
    });
    expect(resources).toEqual([
      {
        kind: 'vars',
        id: 'slack_channel',
        label: 'slack_channel',
        detail: 'string',
        insert: '{{ $vars.slack_channel }}',
      },
    ]);
  });

  it('omits upstream data when the canvas is empty and de-duplicates ids', () => {
    const resources = buildMentionResources({
      workflows: [
        { id: 'wf_1', name: 'One' },
        { id: 'wf_1', name: 'Duplicate' },
      ],
    });
    expect(resources).toHaveLength(1);
    expect(resources[0].label).toBe('One');
  });
});

describe('saved prompts', () => {
  it('stores prompts most recent first and ignores blanks', () => {
    const storage = memoryStorage();
    let prompts = savePrompt('  ', [], storage);
    expect(prompts).toEqual([]);

    prompts = savePrompt('Rebuild the Slack digest', prompts, storage);
    prompts = savePrompt('Add a retry to HTTP', prompts, storage);
    expect(prompts.map((prompt) => prompt.text)).toEqual([
      'Add a retry to HTTP',
      'Rebuild the Slack digest',
    ]);

    // Re-saving an existing prompt moves it to the top instead of duplicating.
    prompts = savePrompt('Rebuild the slack digest', prompts, storage);
    expect(prompts.map((prompt) => prompt.text)).toEqual([
      'Rebuild the slack digest',
      'Add a retry to HTTP',
    ]);
    expect(Object.keys(storage.dump())).toEqual([SAVED_PROMPTS_KEY]);
  });

  it('caps the list and survives corrupt storage', () => {
    let prompts: { id: string; text: string }[] = [];
    for (let index = 0; index < MAX_SAVED_PROMPTS + 5; index += 1) {
      prompts = savePrompt(`Prompt ${index}`, prompts, memoryStorage());
    }
    expect(prompts).toHaveLength(MAX_SAVED_PROMPTS);

    const broken = memoryStorage({ [SAVED_PROMPTS_KEY]: '{not json' });
    expect(loadSavedPrompts(broken)).toEqual([]);
    expect(
      loadSavedPrompts(
        memoryStorage({
          [SAVED_PROMPTS_KEY]: JSON.stringify([{ id: 'a', text: 'ok' }, { nope: true }]),
        }),
      ),
    ).toEqual([{ id: 'a', text: 'ok' }]);
  });
});
