// `@` mentions in the assistant composer. Pure helpers so the picker and the
// textarea stay in sync and stay testable without a DOM.

export type MentionKind =
  | 'workflow'
  | 'node'
  | 'json'
  | 'credential'
  | 'vars'
  | 'template'
  | 'prompt'
  | 'snippet';

export interface MentionResource {
  kind: MentionKind;
  id: string;
  label: string;
  /** Secondary line in the picker (status, node type, prompt preview). */
  detail?: string;
  /** Text inserted into the composer, without the leading space. */
  insert?: string;
}

export interface MentionQuery {
  /** Index of the `@` that opened the picker. */
  start: number;
  query: string;
}

/**
 * The mention being typed at `caret`, or null when the caret is not inside an
 * `@token` (an `@` must start the text or follow whitespace).
 */
export function activeMention(text: string, caret: number): MentionQuery | null {
  if (caret < 0 || caret > text.length) return null;
  let start = caret;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  if (start === caret) return null;
  if (text[start] !== '@') return null;
  const query = text.slice(start + 1, caret);
  // A second `@` or a line break means this is not a mention any more.
  if (/[@\n]/.test(query)) return null;
  return { start, query };
}

function haystack(resource: MentionResource): string {
  return `${resource.label} ${resource.detail ?? ''} ${resource.id}`.toLowerCase();
}

/** Best matches first: label prefix, then label contains, then detail. */
export function filterMentions(
  resources: MentionResource[],
  query: string,
  limit = 8,
): MentionResource[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return resources.slice(0, limit);
  const scored: { resource: MentionResource; score: number }[] = [];
  for (const resource of resources) {
    const label = resource.label.toLowerCase();
    if (label.startsWith(needle)) scored.push({ resource, score: 0 });
    else if (label.includes(needle)) scored.push({ resource, score: 1 });
    else if (haystack(resource).includes(needle)) scored.push({ resource, score: 2 });
  }
  scored.sort((a, b) => a.score - b.score);
  return scored.slice(0, limit).map((entry) => entry.resource);
}

/** How a mention is written into the prompt. */
export function mentionText(resource: MentionResource): string {
  if (resource.insert) return resource.insert;
  switch (resource.kind) {
    case 'workflow':
      return `@${resource.label} (workflow ${resource.id})`;
    case 'node':
      return `@${resource.label} (node ${resource.id})`;
    case 'credential':
      return `@${resource.label} (credential ${resource.id})`;
    case 'vars':
      return resource.insert ?? `{{ $vars.${resource.label} }}`;
    case 'template':
      return `@${resource.label} (template ${resource.id})`;
    case 'json':
      return '{{ $json }}';
    case 'prompt':
    case 'snippet':
      return resource.insert ?? resource.label;
    default:
      return `@${resource.label}`;
  }
}

/** Replace the mention token with the chosen resource, returning the new caret. */
export function applyMention(
  text: string,
  mention: MentionQuery,
  resource: MentionResource,
): { text: string; caret: number } {
  const before = text.slice(0, mention.start);
  const after = text.slice(mention.start + 1 + mention.query.length);
  const insert = /^\s/.test(after) ? mentionText(resource) : `${mentionText(resource)} `;
  return { text: `${before}${insert}${after}`, caret: before.length + insert.length };
}

/** Every resource mentioned in a finished prompt (used for context assembly). */
export function mentionedIds(text: string, resources: MentionResource[]): string[] {
  const ids: string[] = [];
  for (const resource of resources) {
    const token = resource.insert ?? `@${resource.label}`;
    if (text.includes(token) && !ids.includes(resource.id)) ids.push(resource.id);
  }
  return ids;
}
