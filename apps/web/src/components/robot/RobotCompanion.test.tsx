import * as React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import { RobotCompanion } from './RobotCompanion';
import type { RobotCompanionController } from '@/features/robot/use-robot-companion';

vi.mock('next/dynamic', () => ({
  default: () => function MockDynamicCanvas() {
    return null;
  },
}));

function controller(
  overrides: Partial<RobotCompanionController> = {},
): RobotCompanionController {
  return {
    state: 'idle',
    caption: 'Ready when you are',
    levelRef: { current: 0 },
    voiceEnabled: false,
    voiceSupported: true,
    lastReply: '',
    setVoiceEnabled: vi.fn(),
    toggleVoice: vi.fn(),
    voiceActivity: vi.fn(),
    setAudioLevel: vi.fn(),
    requestStarted: vi.fn(),
    responseStreaming: vi.fn(),
    completed: vi.fn(),
    failed: vi.fn(),
    reset: vi.fn(),
    celebrate: vi.fn(),
    sad: vi.fn(),
    replay: vi.fn(),
    clearReply: vi.fn(),
    stopSpeaking: vi.fn(),
    ...overrides,
  };
}

describe('RobotCompanion', () => {
  it('announces the live state and exposes the wave action', () => {
    const celebrate = vi.fn();
    render(
      <RobotCompanion
        controller={controller({ state: 'thinking', caption: 'Thinking it through', celebrate })}
      />,
    );

    expect(screen.getByText('Thinking it through')).not.toBeNull();
    fireEvent.click(screen.getByLabelText('Make the robot wave'));
    expect(celebrate).toHaveBeenCalledTimes(1);
  });

  it('toggles spoken replies and reads the last answer on demand', () => {
    const toggleVoice = vi.fn();
    const replay = vi.fn();
    const ctrl = controller({ lastReply: 'Done.', toggleVoice, replay });
    render(<RobotCompanion controller={ctrl} />);

    fireEvent.click(screen.getByText('Voice off'));
    expect(toggleVoice).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('Read last reply'));
    expect(replay).toHaveBeenCalledTimes(1);
  });

  it('disables unsupported speech output instead of pretending it works', () => {
    render(<RobotCompanion controller={controller({ voiceSupported: false })} />);
    const button = screen.getByText('Voice off').closest('button');
    expect(button?.disabled).toBe(true);
    expect(screen.getByText(/Spoken replies need/)).not.toBeNull();
  });
});
