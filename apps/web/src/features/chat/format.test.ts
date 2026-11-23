import { emptyReplyHint, formatBytes, formatDuration, runFromDone } from './format';
import type { ChatStreamDone } from './types';

function done(overrides: Partial<ChatStreamDone> = {}): ChatStreamDone {
  return {
    session_id: 's1',
    status: 'completed',
    reply: 'hi',
    reply_node_id: 'reply',
    execution_id: 'exec_1',
    workflow_id: 'wf_1',
    steps: [
      {
        node_id: 'reply',
        node_type: 'prompt',
        node_name: 'Reply',
        status: 'succeeded',
        duration_ms: 8,
        error: null,
        reason: null,
      },
    ],
    summary: { total: 2, succeeded: 2, failed: 0, skipped: 0, waiting: 0, duration_ms: 42 },
    error: null,
    model: 'qwen2.5:0.5b-instruct',
    model_skipped: false,
    ...overrides,
  };
}

describe('chat formatting', () => {
  it('formats byte counts', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(900)).toBe('900 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(20 * 1024)).toBe('20 KB');
    expect(formatBytes(3.5 * 1024 * 1024)).toBe('3.5 MB');
  });

  it('formats durations for short and long runs', () => {
    expect(formatDuration(0)).toBe('0 ms');
    expect(formatDuration(842)).toBe('842 ms');
    expect(formatDuration(1200)).toBe('1.2 s');
    expect(formatDuration(12_400)).toBe('12 s');
    expect(formatDuration(65_000)).toBe('1m 5s');
  });

  it('maps a stream payload onto the stored run metadata', () => {
    expect(runFromDone(done())).toEqual({
      execution_id: 'exec_1',
      status: 'completed',
      steps: done().steps,
      duration_ms: 42,
      model: 'qwen2.5:0.5b-instruct',
      modelSkipped: false,
      replyNodeId: 'reply',
      error: null,
    });
  });

  it('explains an empty reply without inventing one', () => {
    expect(emptyReplyHint({ status: 'completed', model_skipped: true, error: null })).toMatch(
      /No model step ran/,
    );
    expect(emptyReplyHint({ status: 'waiting', model_skipped: false, error: null })).toMatch(
      /human approval/,
    );
    expect(emptyReplyHint({ status: 'failed', model_skipped: false, error: 'boom' })).toMatch(
      /failed before it could reply/,
    );
    expect(emptyReplyHint({ status: 'completed', model_skipped: false, error: null })).toMatch(
      /without any text output/,
    );
  });
});
