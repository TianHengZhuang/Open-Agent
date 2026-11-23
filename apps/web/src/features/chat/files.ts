// Attachments are local: the browser reads the file into a data URL, the chat
// turn carries it to the Chat Trigger item, and nothing is uploaded elsewhere.
// (No text extraction/RAG yet — workflows receive the raw data URL.)

import type { ChatAttachment } from './types';

export const MAX_ATTACHMENTS = 6;
export const MAX_ATTACHMENT_BYTES = 4_000_000;

const ALLOWED_PREFIXES = ['image/', 'text/', 'application/pdf', 'application/json', 'text/csv'];

export function validateAttachment(file: { name: string; size: number; type: string }): string | null {
  if (!file.name.trim()) return 'This file has no name.';
  if (file.size <= 0) return `${file.name} is empty.`;
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return `${file.name} is larger than 4 MB. Attach a smaller file.`;
  }
  const type = (file.type || '').toLowerCase();
  if (type && !ALLOWED_PREFIXES.some((prefix) => type.startsWith(prefix))) {
    return `${file.name} is not a supported type (text, CSV, JSON, PDF or image).`;
  }
  return null;
}

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the file.'));
    reader.readAsDataURL(file);
  });
}

export async function readAttachment(file: File): Promise<ChatAttachment> {
  const problem = validateAttachment(file);
  if (problem) throw new Error(problem);
  const dataUrl = await fileToDataUrl(file);
  return {
    name: file.name,
    mimeType: file.type || 'application/octet-stream',
    dataUrl,
    size: file.size,
  };
}

export function attachmentPayload(attachments: ChatAttachment[]): {
  name: string;
  mime_type: string;
  data_url: string;
}[] {
  return attachments.slice(0, MAX_ATTACHMENTS).map((file) => ({
    name: file.name,
    mime_type: file.mimeType,
    data_url: file.dataUrl,
  }));
}
