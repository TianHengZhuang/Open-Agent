'use client';

import * as React from 'react';
import dynamic from 'next/dynamic';
import { Hand, Play, Radio, Sparkles, Volume2, VolumeX } from 'lucide-react';

import type { RobotCompanionController } from '@/features/robot/use-robot-companion';
import type { RobotState } from '@/features/robot/robot-machine';
import { cn } from '@/lib/utils';

const RobotCanvas = dynamic(
  () => import('./RobotCanvas').then((module) => module.RobotCanvas),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-3">
        <span className="relative flex h-12 w-12 items-center justify-center rounded-2xl border border-orange-400/30 bg-orange-500/10">
          <Sparkles className="h-5 w-5 animate-pulse text-orange-300" aria-hidden />
        </span>
        <p className="text-[11px] uppercase tracking-[0.18em] text-orange-200/60">
          Waking the mascot
        </p>
      </div>
    ),
  },
);

const STATE_STYLES: Record<RobotState, string> = {
  idle: 'border-orange-300/25 bg-orange-400/10 text-orange-100',
  listening: 'border-amber-300/40 bg-amber-400/15 text-amber-100',
  thinking: 'border-yellow-300/35 bg-yellow-400/10 text-yellow-100',
  speaking: 'border-orange-300/45 bg-orange-400/15 text-orange-50',
  happy: 'border-amber-300/45 bg-amber-400/15 text-amber-50',
  sad: 'border-orange-500/30 bg-orange-900/30 text-orange-100/80',
  success: 'border-lime-300/45 bg-lime-400/15 text-lime-50',
  error: 'border-red-400/45 bg-red-500/15 text-red-50',
};

class RobotCanvasBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-3 px-6 text-center">
          <div
            className="relative flex h-24 w-24 items-center justify-center rounded-[1.6rem] border-2 border-orange-400/50 bg-gradient-to-b from-orange-400 to-orange-600 shadow-[0_0_40px_rgba(255,122,18,0.25)]"
            aria-hidden
          >
            <div className="flex h-12 w-16 items-center justify-center gap-3 rounded-xl bg-[#0b0806]">
              <span className="h-3 w-3 rounded-full border-2 border-b-transparent border-l-transparent border-orange-200" />
              <span className="h-3 w-3 rounded-full border-2 border-b-transparent border-l-transparent border-orange-200" />
            </div>
          </div>
          <p className="text-xs text-orange-100/70">
            The live 3D view is unavailable on this device. The assistant still works.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

export function RobotCompanion({
  controller,
  className,
  onClose,
}: {
  controller: RobotCompanionController;
  className?: string;
  onClose?: () => void;
}) {
  const { state, caption } = controller;
  const active = state === 'listening' || state === 'thinking' || state === 'speaking';

  return (
    <div
      className={cn(
        'relative flex h-full min-h-0 w-full flex-col overflow-hidden bg-[#100805] text-orange-50',
        className,
      )}
    >
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(80% 55% at 50% 38%, rgba(255,112,20,0.19) 0%, rgba(255,88,8,0.06) 45%, transparent 72%), linear-gradient(180deg, #140a05 0%, #0c0604 100%)',
        }}
        aria-hidden
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.18]"
        style={{
          backgroundImage:
            'radial-gradient(rgba(255,180,110,0.45) 0.7px, transparent 0.7px)',
          backgroundSize: '18px 18px',
          maskImage: 'linear-gradient(180deg, transparent, black 35%, black 75%, transparent)',
        }}
        aria-hidden
      />

      <header className="relative flex h-12 shrink-0 items-center gap-2 border-b border-orange-200/10 px-3">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-orange-300/25 bg-orange-400/10">
          <Radio className="h-3.5 w-3.5 text-orange-300" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px] font-semibold uppercase tracking-[0.16em] text-orange-100/90">
            OpenAgent Companion
          </p>
          <p className="text-[10px] text-orange-100/45">Live 3D mascot</p>
        </div>
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em]',
            active
              ? 'border-orange-300/35 bg-orange-400/10 text-orange-200'
              : 'border-white/10 bg-white/[0.03] text-orange-100/45',
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              active ? 'animate-pulse bg-orange-300' : 'bg-orange-200/35',
            )}
            aria-hidden
          />
          {active ? 'Live' : 'Ready'}
        </span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-1.5 py-1 text-[10px] text-orange-100/50 hover:bg-white/5 hover:text-orange-50 xl:hidden"
            aria-label="Close the 3D companion"
          >
            Close
          </button>
        )}
      </header>

      <div
        className="relative min-h-[280px] flex-1"
        role="img"
        aria-label={`OpenAgent robot mascot. Current state: ${caption}.`}
      >
        <RobotCanvasBoundary>
          <RobotCanvas state={state} levelRef={controller.levelRef} />
        </RobotCanvasBoundary>

        <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center px-3">
          <span
            className={cn(
              'inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-medium tracking-wide backdrop-blur',
              STATE_STYLES[state],
            )}
            aria-live="polite"
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-80" aria-hidden />
            <span className="truncate">{caption}</span>
          </span>
        </div>
      </div>

      <div className="relative shrink-0 border-t border-orange-200/10 bg-black/20 p-2.5 backdrop-blur">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={controller.toggleVoice}
            disabled={!controller.voiceSupported}
            aria-pressed={controller.voiceEnabled}
            title={
              controller.voiceSupported
                ? controller.voiceEnabled
                  ? 'Mute spoken replies'
                  : 'Speak assistant replies aloud'
                : 'Speech synthesis is not available in this browser'
            }
            className={cn(
              'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[10px] font-medium transition-colors',
              controller.voiceEnabled
                ? 'border-orange-300/40 bg-orange-400/15 text-orange-100'
                : 'border-white/10 bg-white/[0.03] text-orange-100/55 hover:bg-white/[0.06] hover:text-orange-50',
              !controller.voiceSupported && 'cursor-not-allowed opacity-40',
            )}
          >
            {controller.voiceEnabled ? (
              <Volume2 className="h-3 w-3" aria-hidden />
            ) : (
              <VolumeX className="h-3 w-3" aria-hidden />
            )}
            {controller.voiceEnabled ? 'Voice on' : 'Voice off'}
          </button>

          <button
            type="button"
            onClick={controller.replay}
            disabled={!controller.lastReply}
            title="Read the last assistant reply"
            className="inline-flex h-7 items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-2.5 text-[10px] font-medium text-orange-100/55 transition-colors hover:bg-white/[0.06] hover:text-orange-50 disabled:cursor-not-allowed disabled:opacity-35"
          >
            <Play className="h-3 w-3" aria-hidden />
            Read last reply
          </button>

          <button
            type="button"
            onClick={controller.celebrate}
            title="Make the robot wave"
            aria-label="Make the robot wave"
            className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-orange-100/55 transition-colors hover:bg-orange-400/10 hover:text-orange-100"
          >
            <Hand className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        <p className="mt-1.5 px-0.5 text-[9px] leading-4 text-orange-100/35">
          {controller.voiceSupported
            ? 'Voice uses this browser’s speech engine. Mic energy drives the face and ring.'
            : 'Spoken replies need a browser with speech synthesis.'}
        </p>
      </div>
    </div>
  );
}
