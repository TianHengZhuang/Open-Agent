// Small pure helpers for the chat surface (kept separate so they are testable).

import type { ChatRunMeta, ChatStreamDone } from './types';

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(ms: number): string {
  const value = Number.isFinite(ms) ? Math.max(0, Math.round(ms)) : 0;
  if (value < 1000) return `${value} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`;
}

export function runFromDone(done: ChatStreamDone): ChatRunMeta {
  return {
    execution_id: done.execution_id ?? null,
    status: done.status || 'completed',
    steps: Array.isArray(done.steps) ? done.steps : [],
    duration_ms: done.summary?.duration_ms ?? 0,
    model: done.model || '',
    modelSkipped: Boolean(done.model_skipped),
    replyNodeId: done.reply_node_id ?? null,
    error: done.error ?? null,
  };
}

/**
 * What to show instead of nothing when a run produced no text. Each branch
 * names the real cause — no invented "thinking" or placeholder replies.
 */
export function emptyReplyHint(
  done: Pick<ChatStreamDone, 'status' | 'model_skipped' | 'error'>,
): string {
  if (done.error) return 'The workflow failed before it could reply. Open the trace below for the failing step.';
  if (done.status === 'waiting') {
    return 'This run is waiting for a human approval, so there is no reply yet. Continue it from Executions.';
  }
  if (done.model_skipped) {
    return 'No model step ran, so there is no reply to show. Add an **AI Prompt** node after the Chat Trigger and pick a local model in Settings → Copilot.';
  }
  return 'The run finished without any text output. Add an **AI Prompt** node (or emit a `text` field) so the chat has something to say.';
}
