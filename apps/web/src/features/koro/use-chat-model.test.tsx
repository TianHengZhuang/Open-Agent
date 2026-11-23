import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useChatModelOverride } from './use-chat-model';

describe('useChatModelOverride', () => {
  it('holds an override and clears it on request', () => {
    const { result } = renderHook(() => useChatModelOverride('chat-a'));

    expect(result.current.override).toBeNull();
    act(() =>
      result.current.setOverride({ model: 'llama3.2:3b', base_url: 'http://127.0.0.1:11434/v1' }),
    );
    expect(result.current.override?.model).toBe('llama3.2:3b');

    act(() => result.current.setOverride(null));
    expect(result.current.override).toBeNull();
  });

  it('drops the override when the chat changes', () => {
    const { result, rerender } = renderHook(
      ({ chat }: { chat: string | null }) => useChatModelOverride(chat),
      { initialProps: { chat: 'chat-a' } },
    );

    act(() =>
      result.current.setOverride({ model: 'qwen2.5:7b', base_url: 'http://localhost:11434/v1' }),
    );
    expect(result.current.override).not.toBeNull();

    rerender({ chat: 'chat-b' });
    expect(result.current.override).toBeNull();
  });
});
