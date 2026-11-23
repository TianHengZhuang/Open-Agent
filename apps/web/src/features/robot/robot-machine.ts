/**
 * The robot's public state contract. These states are driven by the assistant
 * workspace, not by the 3D scene: the scene only renders them.
 */
export type RobotState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'happy'
  | 'sad'
  | 'success'
  | 'error';

export type RobotEvent =
  | { type: 'VOICE_START' }
  | { type: 'VOICE_STOP' }
  | { type: 'REQUEST_START' }
  | { type: 'RESPONSE_STREAM' }
  | { type: 'RESPONSE_DONE'; ok: boolean }
  | { type: 'SPEECH_START' }
  | { type: 'SPEECH_END' }
  | { type: 'ERROR' }
  | { type: 'CELEBRATE' }
  | { type: 'SAD' }
  | { type: 'RESET' };

/** States that return to idle on their own. */
export const TRANSIENT_ROBOT_STATES: ReadonlySet<RobotState> = new Set([
  'happy',
  'sad',
  'success',
  'error',
]);

/**
 * Pure transition table. Kept separate from React so the interaction contract
 * can be tested without WebGL, audio or the network.
 */
export function nextRobotState(current: RobotState, event: RobotEvent): RobotState {
  switch (event.type) {
    case 'VOICE_START':
      return 'listening';
    case 'VOICE_STOP':
      // The request-start event normally follows immediately. A bare stop
      // (the user cancelled dictation) returns to neutral.
      return current === 'listening' ? 'idle' : current;
    case 'REQUEST_START':
    case 'RESPONSE_STREAM':
      return 'thinking';
    case 'RESPONSE_DONE':
      return event.ok ? 'success' : 'error';
    case 'SPEECH_START':
      return 'speaking';
    case 'SPEECH_END':
      return current === 'speaking' ? 'idle' : current;
    case 'ERROR':
      return 'error';
    case 'CELEBRATE':
      return 'happy';
    case 'SAD':
      return 'sad';
    case 'RESET':
      return 'idle';
    default:
      return current;
  }
}

export const ROBOT_STATE_COPY: Record<RobotState, string> = {
  idle: 'Ready when you are',
  listening: 'I’m listening',
  thinking: 'Thinking it through',
  speaking: 'Speaking',
  happy: 'That’s fun',
  sad: 'That didn’t land',
  success: 'Done — nice work',
  error: 'Something went wrong',
};

/**
 * SpeechSynthesis reads punctuation and code badly. Strip the Markdown shapes
 * that the assistant emits while keeping the words a person would say.
 */
export function speechText(markdown: string, limit = 700): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/[*_~>#|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit);
}

export function clampAudioLevel(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Deterministic mouth energy for SpeechSynthesis boundary events. */
export function boundaryEnergy(word: string): number {
  const letters = word.replace(/[^a-z0-9]/gi, '').length;
  if (letters === 0) return 0.25;
  return clampAudioLevel(0.36 + Math.min(letters, 14) * 0.035);
}
