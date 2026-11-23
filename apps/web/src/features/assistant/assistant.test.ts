import { describe, expect, it } from 'vitest';
import {
  activeMention,
  applyMention,
  filterMentions,
  mentionedIds,
  mentionText,
  type MentionResource,
} from './mentions';
import {
  attachmentProblem,
  attachmentsToPrompt,
  withAttachments,
  type AssistantAttachment,
} from './attachments';
import {
  exportFilename,
  filterChats,
  groupChats,
  sortChats,
  togglePin,
  transcriptMarkdown,
} from './chat-list';

const resources: MentionResource[] = [
  { kind: 'workflow', id: 'wf_1', label: 'Support triage', detail: 'ACTIVE' },
  { kind: 'workflow', id: 'wf_2', label: 'Invoice review', detail: 'DRAFT' },
  { kind: 'node', id: 'n1', label: 'Normalize', detail: 'set' },
  { kind: 'snippet', id: 's1', label: 'Build a webhook to Slack', insert: 'Build a webhook to Slack' },
];

describe('mentions', () => {
  it('detects the mention token at the caret', () => {
    const text = 'Compare @inv';
    expect(activeMention(text, text.length)).toEqual({ start: 8, query: 'inv' });
  });

  it('ignores an @ glued to a word and whitespace-ends the token', () => {
    expect(activeMention('mail me at me@example', 22)).toBeNull();
    expect(activeMention('@support triage', 12)).toBeNull();
  });

  it('opens an empty picker for a bare @', () => {
    expect(activeMention('look at @', 9)).toEqual({ start: 8, query: '' });
  });

  it('ranks label prefixes before detail matches', () => {
    const matches = filterMentions(resources, 'inv');
    expect(matches[0].id).toBe('wf_2');
    const nodeMatch = filterMentions(resources, 'set');
    expect(nodeMatch[0].id).toBe('n1');
  });

  it('replaces the token and returns the new caret', () => {
    const text = 'Run @sup now';
    const mention = activeMention(text, 8)!;
    const result = applyMention(text, mention, resources[0]);
    expect(result.text).toBe('Run @Support triage (workflow wf_1) now');
    expect(result.caret).toBe('Run @Support triage (workflow wf_1)'.length);
  });

  it('formats credentials, templates and upstream data', () => {
    expect(
      mentionText({ kind: 'credential', id: 'c1', label: 'Slack production' }),
    ).toBe('@Slack production (credential c1)');
    expect(mentionText({ kind: 'template', id: 't1', label: 'Order intake' })).toBe(
      '@Order intake (template t1)',
    );
    expect(mentionText({ kind: 'json', id: '$json', label: 'Upstream data' })).toBe(
      '{{ $json }}',
    );
    expect(
      mentionText({ kind: 'prompt', id: 'p1', label: 'Add retries', insert: 'Add retries' }),
    ).toBe('Add retries');
    expect(mentionText({ kind: 'vars', id: 'slack_channel', label: 'slack_channel' })).toBe(
      '{{ $vars.slack_channel }}',
    );
  });

  it('writes snippets verbatim and finds mentioned ids', () => {
    expect(mentionText(resources[3])).toBe('Build a webhook to Slack');
    const prompt = 'Use @Support triage (workflow wf_1) please';
    expect(mentionedIds(prompt, resources)).toEqual(['wf_1']);
  });
});

describe('attachments', () => {
  const csv = 'name,total\nAda,10\n';

  it('accepts text-like files and rejects binaries', () => {
    expect(attachmentProblem({ name: 'report.csv', size: 12, type: 'text/csv' })).toBeNull();
    expect(attachmentProblem({ name: 'flow.json', size: 12, type: 'application/json' })).toBeNull();
    expect(attachmentProblem({ name: 'mystery', size: 12, type: 'application/zip' })).toContain(
      'not text',
    );
    expect(attachmentProblem({ name: 'big.txt', size: 2_000_000, type: 'text/plain' })).toContain(
      '512 KB',
    );
  });

  it('folds files into a delimited, capped block', () => {
    const files: AssistantAttachment[] = [
      { id: 'a', name: 'report.csv', size: csv.length, text: csv },
      { id: 'b', name: 'notes.md', size: 400, text: 'x'.repeat(20_000) },
    ];
    const block = attachmentsToPrompt(files);
    expect(block).toContain('--- report.csv ---');
    expect(block).toContain('Ada,10');
    expect(block).toContain('…(truncated)');
    expect(block.length).toBeLessThan(30_000);
  });

  it('leaves the message untouched when nothing is attached', () => {
    expect(withAttachments('hello', [])).toBe('hello');
    expect(withAttachments('hello', [{ id: 'a', name: 'a.txt', size: 1, text: 'body' }])).toContain(
      'body',
    );
  });
});

describe('chat list', () => {
  const now = new Date('2026-11-22T12:00:00Z');
  const chats = [
    { id: 'a', title: 'Slack digest', message_count: 4, updated_at: '2026-11-22T09:00:00Z' },
    { id: 'b', title: 'Invoice review', message_count: 2, updated_at: '2026-11-21T09:00:00Z' },
    { id: 'c', title: 'Support triage', message_count: 6, updated_at: '2026-11-10T09:00:00Z' },
  ];

  it('filters by title', () => {
    expect(filterChats(chats, 'inv').map((chat) => chat.id)).toEqual(['b']);
    expect(filterChats(chats, '   ')).toHaveLength(3);
  });

  it('puts pinned chats first, then newest', () => {
    expect(sortChats(chats, ['c']).map((chat) => chat.id)).toEqual(['c', 'a', 'b']);
    expect(sortChats(chats, []).map((chat) => chat.id)).toEqual(['a', 'b', 'c']);
    expect(togglePin(['a'], 'b')).toEqual(['a', 'b']);
    expect(togglePin(['a', 'b'], 'a')).toEqual(['b']);
  });

  it('groups chats into today, yesterday and earlier', () => {
    const groups = groupChats(sortChats(chats, []), now);
    expect(groups.map((group) => group.label)).toEqual(['Today', 'Yesterday', 'Earlier']);
  });

  it('exports a readable transcript', () => {
    const markdown = transcriptMarkdown('Slack digest', [
      { role: 'user', content: 'Build it' },
      { role: 'assistant', content: 'Done.', created_at: '2026-11-22T09:00:00Z' },
    ]);
    expect(markdown).toContain('# Slack digest');
    expect(markdown).toContain('## You');
    expect(markdown).toContain('## Assistant · 2026-11-22T09:00:00Z');
    expect(exportFilename('Slack digest!')).toBe('slack-digest.md');
    expect(exportFilename('   ')).toBe('chat.md');
  });
});
