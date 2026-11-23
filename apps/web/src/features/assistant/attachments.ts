// Files attached in the assistant composer. The copilot only understands text,
// so text-like files are read in the browser and folded into the prompt. Images
// and PDFs are rejected with a clear reason instead of being uploaded.

export interface AssistantAttachment {
  id: string;
  name: string;
  size: number;
  text: string;
}

export interface SkippedAttachment {
  name: string;
  reason: string;
}

export const MAX_ATTACHMENTS = 4;
export const MAX_ATTACHMENT_BYTES = 512 * 1024;
export const MAX_ATTACHMENT_CHARS = 8_000;
export const MAX_CONTEXT_CHARS = 24_000;

const TEXT_EXTENSIONS = [
  '.txt', '.md', '.csv', '.json', '.yaml', '.yml', '.xml', '.log', '.tsv', '.env', '.ini',
];

const TEXT_MIME = ['application/json', 'application/xml', 'application/x-yaml', 'text/yaml'];

/** Return a short reason the file cannot be used, or null when it can. */
export function attachmentProblem(file: { name: string; size: number; type: string }): string | null {
  if (!file.name.trim()) return 'This file has no name.';
  if (file.size <= 0) return `${file.name} is empty.`;
  if (file.size > MAX_ATTACHMENT_BYTES) return `${file.name} is larger than 512 KB.`;
  const name = file.name.toLowerCase();
  const type = (file.type || '').toLowerCase();
  const textLike =
    TEXT_EXTENSIONS.some((extension) => name.endsWith(extension)) ||
    type.startsWith('text/') ||
    TEXT_MIME.includes(type);
  if (!textLike) {
    return `${file.name} is not text. Attach JSON, CSV, YAML, XML, logs or plain text.`;
  }
  return null;
}

export interface ReadAttachmentsResult {
  accepted: AssistantAttachment[];
  skipped: SkippedAttachment[];
}

let counter = 0;

function nextId(): string {
  counter += 1;
  return `att_${Date.now().toString(36)}_${counter}`;
}

/** Read the text-like files, reporting anything that was left out. */
export async function readAttachments(
  files: File[],
  existing: AssistantAttachment[] = [],
): Promise<ReadAttachmentsResult> {
  const accepted: AssistantAttachment[] = [];
  const skipped: SkippedAttachment[] = [];
  let room = MAX_ATTACHMENTS - existing.length;

  for (const file of files) {
    if (room <= 0) {
      skipped.push({ name: file.name, reason: `Only ${MAX_ATTACHMENTS} files per message.` });
      continue;
    }
    const problem = attachmentProblem(file);
    if (problem) {
      skipped.push({ name: file.name, reason: problem });
      continue;
    }
    try {
      const text = await file.text();
      accepted.push({ id: nextId(), name: file.name, size: file.size, text });
      room -= 1;
    } catch {
      skipped.push({ name: file.name, reason: 'The file could not be read.' });
    }
  }

  return { accepted, skipped };
}

/** Fold attachments into one prompt block, size-capped and clearly delimited. */
export function attachmentsToPrompt(attachments: AssistantAttachment[]): string {
  if (attachments.length === 0) return '';
  const parts: string[] = ['Attached files from the user (read-only):'];
  let budget = MAX_CONTEXT_CHARS;

  for (const attachment of attachments) {
    if (budget <= 0) break;
    const slice = attachment.text.slice(0, Math.min(MAX_ATTACHMENT_CHARS, budget));
    budget -= slice.length;
    const clipped = slice.length < attachment.text.length ? '\n…(truncated)' : '';
    parts.push(`--- ${attachment.name} ---\n${slice}${clipped}`);
  }

  return parts.join('\n\n');
}

/** The message the backend receives: the prompt plus any attached context. */
export function withAttachments(message: string, attachments: AssistantAttachment[]): string {
  const block = attachmentsToPrompt(attachments);
  return block ? `${message}\n\n${block}` : message;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
