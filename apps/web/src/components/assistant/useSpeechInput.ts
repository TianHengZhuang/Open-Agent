'use client';

import * as React from 'react';

// Minimal shape of the browser speech-to-text API (Chrome/Edge/Safari expose
// it as webkitSpeechRecognition). Typed locally because the DOM lib does not
// declare it yet.

interface SpeechAlternative {
  transcript: string;
}

interface SpeechResult {
  0: SpeechAlternative;
  isFinal: boolean;
}

interface SpeechEvent {
  resultIndex: number;
  results: { length: number; [index: number]: SpeechResult };
}

interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechEvent) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
}

type RecognitionCtor = new () => Recognition;

export interface SpeechInputOptions {
  onStart?: () => void;
  onStop?: () => void;
  onError?: (message: string) => void;
  /** Real microphone RMS, throttled to roughly 30 fps. */
  onLevel?: (level: number) => void;
}

interface AudioMeter {
  context: AudioContext;
  stream: MediaStream;
  source: MediaStreamAudioSourceNode;
  analyser: AnalyserNode;
  frame: number;
}

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const scope = window as unknown as Record<string, unknown>;
  const ctor = scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
  return typeof ctor === 'function' ? (ctor as RecognitionCtor) : null;
}

/**
 * Dictation for the assistant composer. `onTranscript` receives the full
 * utterance so far; the caller decides how to merge it into the draft.
 */
export function useSpeechInput(
  onTranscript: (text: string) => void,
  options: SpeechInputOptions = {},
) {
  const [listening, setListening] = React.useState(false);
  const [supported] = React.useState(() => recognitionCtor() !== null);
  const [error, setError] = React.useState<string | null>(null);
  const recognitionRef = React.useRef<Recognition | null>(null);
  const finalRef = React.useRef('');
  const callbackRef = React.useRef(onTranscript);
  const optionsRef = React.useRef(options);
  const listeningRef = React.useRef(false);
  const meterRef = React.useRef<AudioMeter | null>(null);
  callbackRef.current = onTranscript;
  optionsRef.current = options;

  const stopMeter = React.useCallback(() => {
    const meter = meterRef.current;
    meterRef.current = null;
    if (!meter) return;
    cancelAnimationFrame(meter.frame);
    meter.stream.getTracks().forEach((track) => track.stop());
    try {
      meter.source.disconnect();
    } catch {
      // Already disconnected.
    }
    void meter.context.close().catch(() => undefined);
  }, []);

  const finish = React.useCallback(() => {
    if (!listeningRef.current) {
      stopMeter();
      return;
    }
    listeningRef.current = false;
    setListening(false);
    stopMeter();
    optionsRef.current.onStop?.();
  }, [stopMeter]);

  /**
   * Recognition does not expose its microphone stream, so ask for the same
   * input separately for visualisation. Failure here must never block
   * dictation: the robot simply shows no waveform energy.
   */
  const startMeter = React.useCallback(async () => {
    if (!optionsRef.current.onLevel) return;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (!listeningRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const AudioCtor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtor) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const context = new AudioCtor();
      await context.resume().catch(() => undefined);
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.72;
      source.connect(analyser);
      const meter: AudioMeter = { context, stream, source, analyser, frame: 0 };
      meterRef.current = meter;
      const samples = new Uint8Array(analyser.fftSize);
      let lastEmit = 0;
      const tick = (now: number) => {
        if (meterRef.current !== meter) return;
        if (now - lastEmit > 32) {
          lastEmit = now;
          analyser.getByteTimeDomainData(samples);
          let sum = 0;
          for (let index = 0; index < samples.length; index += 1) {
            const centred = (samples[index] - 128) / 128;
            sum += centred * centred;
          }
          const rms = Math.sqrt(sum / samples.length);
          optionsRef.current.onLevel?.(Math.min(1, rms * 4.2));
        }
        meter.frame = requestAnimationFrame(tick);
      };
      meter.frame = requestAnimationFrame(tick);
    } catch {
      // Visualisation is optional and must not turn into a dictation error.
    }
  }, []);

  const stop = React.useCallback(() => {
    recognitionRef.current?.stop();
    finish();
  }, [finish]);

  const start = React.useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor) return;
    setError(null);
    finalRef.current = '';
    const recognition = new Ctor();
    recognition.lang = typeof navigator !== 'undefined' ? navigator.language || 'en-US' : 'en-US';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      let interim = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result[0]?.transcript ?? '';
        if (result.isFinal) finalRef.current += text;
        else interim += text;
      }
      callbackRef.current(`${finalRef.current}${interim}`.trim());
    };
    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return;
      const message =
        event.error === 'not-allowed'
          ? 'Microphone access was blocked.'
          : event.error === 'no-speech'
            ? 'I did not hear anything.'
            : 'Dictation stopped.';
      setError(message);
      finish();
      optionsRef.current.onError?.(message);
    };
    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      finish();
    };
    recognitionRef.current?.abort();
    recognitionRef.current = recognition;
    try {
      listeningRef.current = true;
      recognition.start();
      setListening(true);
      optionsRef.current.onStart?.();
      void startMeter();
    } catch {
      setError('Could not start the microphone.');
      listeningRef.current = false;
      setListening(false);
      stopMeter();
      optionsRef.current.onError?.('Could not start the microphone.');
    }
  }, [finish, startMeter, stopMeter]);

  const toggle = React.useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  React.useEffect(
    () => () => {
      listeningRef.current = false;
      recognitionRef.current?.abort();
      stopMeter();
    },
    [stopMeter],
  );

  return { listening, supported, error, toggle, stop, clearError: () => setError(null) };
}
