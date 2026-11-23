import {
  boundaryEnergy,
  clampAudioLevel,
  nextRobotState,
  speechText,
  type RobotState,
} from './robot-machine';

describe('robot state machine', () => {
  it('moves through listening, thinking and success', () => {
    let state: RobotState = 'idle';
    state = nextRobotState(state, { type: 'VOICE_START' });
    expect(state).toBe('listening');
    state = nextRobotState(state, { type: 'VOICE_STOP' });
    expect(state).toBe('idle');
    state = nextRobotState(state, { type: 'REQUEST_START' });
    expect(state).toBe('thinking');
    state = nextRobotState(state, { type: 'RESPONSE_DONE', ok: true });
    expect(state).toBe('success');
  });

  it('keeps thinking while the answer streams', () => {
    expect(nextRobotState('thinking', { type: 'RESPONSE_STREAM' })).toBe('thinking');
  });

  it('does not reset thinking when dictation ends after send', () => {
    expect(nextRobotState('thinking', { type: 'VOICE_STOP' })).toBe('thinking');
  });

  it('enters speaking only when audio actually starts', () => {
    expect(nextRobotState('success', { type: 'SPEECH_START' })).toBe('speaking');
    expect(nextRobotState('speaking', { type: 'SPEECH_END' })).toBe('idle');
    expect(nextRobotState('idle', { type: 'SPEECH_END' })).toBe('idle');
  });

  it('surfaces failures and can always reset', () => {
    expect(nextRobotState('thinking', { type: 'RESPONSE_DONE', ok: false })).toBe('error');
    expect(nextRobotState('error', { type: 'ERROR' })).toBe('error');
    expect(nextRobotState('speaking', { type: 'RESET' })).toBe('idle');
  });
});

describe('robot audio helpers', () => {
  it('clamps microphone energy into the render range', () => {
    expect(clampAudioLevel(-1)).toBe(0);
    expect(clampAudioLevel(2)).toBe(1);
    expect(clampAudioLevel(Number.NaN)).toBe(0);
    expect(clampAudioLevel(0.42)).toBeCloseTo(0.42);
  });

  it('turns speech boundaries into deterministic mouth energy', () => {
    expect(boundaryEnergy('hi')).toBeLessThan(boundaryEnergy('automation'));
    expect(boundaryEnergy('...')).toBe(0.25);
    expect(boundaryEnergy('supercalifragilistic')).toBeLessThanOrEqual(1);
  });

  it('strips Markdown before speaking', () => {
    const text = speechText(
      '# Done\n\n```ts\nconst secret = 1;\n```\nUse [the docs](https://example.com). **Great** `job`.',
    );
    expect(text).toBe('Done Use the docs. Great job.');
    expect(text).not.toContain('const secret');
  });
});
