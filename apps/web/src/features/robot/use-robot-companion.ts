'use client';

import * as React from 'react';

import {
  ROBOT_STATE_COPY,
  TRANSIENT_ROBOT_STATES,
  boundaryEnergy,
  clampAudioLevel,
  nextRobotState,
  speechText,
  type RobotEvent,
  type RobotState,
} from './robot-machine';

export interface RobotVoiceActivity {
  listening: boolean;
  /** RMS energy from the microphone, 0..1. */
  level?: number;
  error?: string | null;
}

export interface RobotCompanionController {
  state: RobotState;
  caption: string;
  /** Mutable energy shared with the render loop (no 60 fps React renders). */
  levelRef: { current: number };
  voiceEnabled: boolean;
  voiceSupported: boolean;
  lastReply: string;
  setVoiceEnabled: (enabled: boolean) => void;
  toggleVoice: () => void;
  voiceActivity: (activity: RobotVoiceActivity) => void;
  /** Push real TTS analyser energy when an audio element/provider is wired. */
  setAudioLevel: (level: number) => void;
  requestStarted: () => void;
  responseStreaming: () => void;
  completed: (reply: string, ok?: boolean) => void;
  failed: () => void;
  reset: () => void;
  celebrate: () => void;
  sad: () => void;
  replay: () => void;
  clearReply: () => void;
  stopSpeaking: () => void;
}

const VOICE_PREFERENCE_KEY = 'oa:robot-voice';
const TRANSIENT_MS = 2600;

function robotReducer(state: RobotState, event: RobotEvent): RobotState {
  return nextRobotState(state, event);
}

function preferredVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter((voice) => /^en([-_]|$)/i.test(voice.lang));
  return (
    english.find((voice) => /samantha|zira|aria|female|google us english/i.test(voice.name)) ??
    english[0] ??
    voices[0] ??
    null
  );
}

/**
 * Owns the robot's interaction state and its audio bridge:
 * - microphone energy arrives from the dictation hook;
 * - browser speech synthesis emits real start/boundary/end events;
 * - mouth energy is derived from those boundaries (not random noise);
 * - an external analyser can push real TTS energy into `levelRef` later.
 */
export function useRobotCompanion(): RobotCompanionController {
  const [state, dispatch] = React.useReducer(robotReducer, 'idle');
  const [voiceEnabled, setVoiceEnabledState] = React.useState(false);
  const [voiceSupported, setVoiceSupported] = React.useState(false);
  const [lastReply, setLastReply] = React.useState('');
  const levelRef = React.useRef(0);
  const listeningRef = React.useRef(false);
  const voiceEnabledRef = React.useRef(false);
  const utteranceRef = React.useRef<SpeechSynthesisUtterance | null>(null);
  const speakTimerRef = React.useRef<number | null>(null);
  const speechTokenRef = React.useRef(0);

  React.useEffect(() => {
    setVoiceSupported(
      typeof window !== 'undefined' && 'speechSynthesis' in window,
    );
    try {
      setVoiceEnabledState(window.localStorage.getItem(VOICE_PREFERENCE_KEY) === '1');
    } catch {
      // Storage can be unavailable in private mode; keep voice off.
    }
  }, []);

  React.useEffect(() => {
    voiceEnabledRef.current = voiceEnabled;
  }, [voiceEnabled]);

  // A short-lived emotional state (success/happy/error) settles back to idle.
  React.useEffect(() => {
    if (!TRANSIENT_ROBOT_STATES.has(state)) return;
    const timer = window.setTimeout(() => dispatch({ type: 'RESET' }), TRANSIENT_MS);
    return () => window.clearTimeout(timer);
  }, [state]);

  // While synthesized speech plays, let each boundary pulse decay smoothly.
  React.useEffect(() => {
    if (state !== 'speaking') return;
    let frame = 0;
    let previous = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - previous) / 1000);
      previous = now;
      levelRef.current = Math.max(0, levelRef.current - dt * 1.8);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [state]);

  const stopSpeaking = React.useCallback(() => {
    speechTokenRef.current += 1;
    if (speakTimerRef.current !== null) {
      window.clearTimeout(speakTimerRef.current);
      speakTimerRef.current = null;
    }
    levelRef.current = 0;
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    utteranceRef.current = null;
    dispatch({ type: 'SPEECH_END' });
  }, []);

  const speakNow = React.useCallback((reply: string) => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
    const text = speechText(reply);
    if (!text) return;

    const token = speechTokenRef.current + 1;
    speechTokenRef.current = token;
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-US';
    utterance.rate = 1.03;
    utterance.pitch = 1.08;
    utterance.volume = 1;
    const voice = preferredVoice(window.speechSynthesis.getVoices());
    if (voice) utterance.voice = voice;

    utterance.onstart = () => {
      if (speechTokenRef.current !== token) return;
      levelRef.current = 0.5;
      dispatch({ type: 'SPEECH_START' });
    };
    utterance.onboundary = (event) => {
      if (speechTokenRef.current !== token) return;
      const length = event.charLength || 12;
      const word = text.slice(event.charIndex, event.charIndex + length);
      levelRef.current = boundaryEnergy(word);
    };
    utterance.onend = () => {
      if (speechTokenRef.current !== token) return;
      levelRef.current = 0;
      utteranceRef.current = null;
      dispatch({ type: 'SPEECH_END' });
    };
    utterance.onerror = () => {
      if (speechTokenRef.current !== token) return;
      levelRef.current = 0;
      utteranceRef.current = null;
      dispatch({ type: 'SPEECH_END' });
    };

    utteranceRef.current = utterance;
    window.speechSynthesis.speak(utterance);
  }, []);

  const setVoiceEnabled = React.useCallback((enabled: boolean) => {
    setVoiceEnabledState(enabled);
    voiceEnabledRef.current = enabled;
    try {
      window.localStorage.setItem(VOICE_PREFERENCE_KEY, enabled ? '1' : '0');
    } catch {
      // Preference is best-effort.
    }
    if (!enabled) {
      speechTokenRef.current += 1;
      if (speakTimerRef.current !== null) {
        window.clearTimeout(speakTimerRef.current);
        speakTimerRef.current = null;
      }
      levelRef.current = 0;
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      utteranceRef.current = null;
      dispatch({ type: 'SPEECH_END' });
    }
  }, []);

  const toggleVoice = React.useCallback(() => {
    setVoiceEnabled(!voiceEnabledRef.current);
  }, [setVoiceEnabled]);

  const voiceActivity = React.useCallback((activity: RobotVoiceActivity) => {
    levelRef.current = clampAudioLevel(activity.level ?? 0);
    if (activity.listening && !listeningRef.current) {
      listeningRef.current = true;
      dispatch({ type: 'VOICE_START' });
    } else if (!activity.listening && listeningRef.current) {
      listeningRef.current = false;
      levelRef.current = 0;
      dispatch({ type: 'VOICE_STOP' });
    }
    if (activity.error) dispatch({ type: 'ERROR' });
  }, []);

  const setAudioLevel = React.useCallback((level: number) => {
    levelRef.current = clampAudioLevel(level);
  }, []);

  const requestStarted = React.useCallback(() => {
    speechTokenRef.current += 1;
    if (speakTimerRef.current !== null) {
      window.clearTimeout(speakTimerRef.current);
      speakTimerRef.current = null;
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    levelRef.current = 0;
    listeningRef.current = false;
    dispatch({ type: 'REQUEST_START' });
  }, []);

  const responseStreaming = React.useCallback(() => {
    dispatch({ type: 'RESPONSE_STREAM' });
  }, []);

  const completed = React.useCallback(
    (reply: string, ok = true) => {
      setLastReply(reply);
      dispatch({ type: 'RESPONSE_DONE', ok });
      if (!ok || !voiceEnabledRef.current) return;
      // Let the success reaction land before the voice begins.
      if (speakTimerRef.current !== null) window.clearTimeout(speakTimerRef.current);
      speakTimerRef.current = window.setTimeout(() => {
        speakTimerRef.current = null;
        speakNow(reply);
      }, 260);
    },
    [speakNow],
  );

  const failed = React.useCallback(() => {
    dispatch({ type: 'ERROR' });
  }, []);

  const reset = React.useCallback(() => {
    listeningRef.current = false;
    levelRef.current = 0;
    dispatch({ type: 'RESET' });
  }, []);

  const celebrate = React.useCallback(() => {
    dispatch({ type: 'CELEBRATE' });
  }, []);

  const sad = React.useCallback(() => {
    dispatch({ type: 'SAD' });
  }, []);

  const replay = React.useCallback(() => {
    if (lastReply) speakNow(lastReply);
  }, [lastReply, speakNow]);

  const clearReply = React.useCallback(() => {
    setLastReply('');
  }, []);

  React.useEffect(
    () => () => {
      speechTokenRef.current += 1;
      if (speakTimerRef.current !== null) window.clearTimeout(speakTimerRef.current);
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
    },
    [],
  );

  return {
    state,
    caption: ROBOT_STATE_COPY[state],
    levelRef,
    voiceEnabled,
    voiceSupported,
    lastReply,
    setVoiceEnabled,
    toggleVoice,
    voiceActivity,
    setAudioLevel,
    requestStarted,
    responseStreaming,
    completed,
    failed,
    reset,
    celebrate,
    sad,
    replay,
    clearReply,
    stopSpeaking,
  };
}
