// Chat history helpers for the assistant sidebar: filtering, pinning, grouping
// and export. Pure functions so the list behaviour is testable.

export interface ChatListItem {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

export const HISTORY_GROUPS = ['Today', 'Yesterday', 'Earlier'] as const;
export type HistoryGroup = (typeof HISTORY_GROUPS)[number];

/** Local title/chats filter (case-insensitive, whitespace tolerant). */
export function filterChats<T extends ChatListItem>(items: T[], query: string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => item.title.toLowerCase().includes(needle));
}

/** Pinned chats first, then most recently updated. */
export function sortChats<T extends ChatListItem>(items: T[], pinned: string[]): T[] {
  const pinnedSet = new Set(pinned);
  return [...items].sort((a, b) => {
    const aPinned = pinnedSet.has(a.id);
    const bPinned = pinnedSet.has(b.id);
    if (aPinned !== bPinned) return aPinned ? -1 : 1;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}

export function togglePin(pinned: string[], id: string): string[] {
  return pinned.includes(id) ? pinned.filter((value) => value !== id) : [...pinned, id];
}

export function groupFor(iso: string, now = new Date()): HistoryGroup {
  const date = new Date(iso);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (date >= startOfToday) return 'Today';
  if (date >= new Date(startOfToday.getTime() - 86_400_000)) return 'Yesterday';
  return 'Earlier';
}

export function groupChats<T extends ChatListItem>(
  items: T[],
  now = new Date(),
): { label: HistoryGroup; items: T[] }[] {
  return HISTORY_GROUPS.map((label) => ({
    label,
    items: items.filter((item) => groupFor(item.updated_at, now) === label),
  })).filter((group) => group.items.length > 0);
}

export interface TranscriptMessage {
  role: string;
  content: string;
  created_at?: string;
}

/** A portable Markdown copy of one conversation. */
export function transcriptMarkdown(title: string, messages: TranscriptMessage[]): string {
  const lines = [`# ${title}`, '', `_Exported ${new Date().toISOString()}_`, ''];
  for (const message of messages) {
    const who = message.role === 'user' ? 'You' : 'Assistant';
    const stamp = message.created_at ? ` · ${message.created_at}` : '';
    lines.push(`## ${who}${stamp}`, '', message.content.trim(), '');
  }
  return lines.join('\n');
}

export function downloadTextFile(filename: string, content: string, mime = 'text/markdown'): void {
  if (typeof window === 'undefined') return;
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** A filesystem-safe file name for an exported chat. */
export function exportFilename(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
  return `${slug || 'chat'}.md`;
}
