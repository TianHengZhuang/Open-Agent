'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * OpenAgent brand mark: two nodes feeding a branch, drawn as one continuous
 * stroke — the n8n-style "workflow" glyph. Rendered in brand pink (#EA4B71),
 * with a lighter accent in dark mode.
 */
export function LogoMark({
  className,
  title = 'OpenAgent',
}: {
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 40 32"
      fill="none"
      role="img"
      aria-label={title}
      className={cn('text-[#EA4B71] dark:text-[#FF91AC]', className)}
    >
      <g stroke="currentColor" strokeWidth={3.5} strokeLinecap="round">
        <circle cx="7.5" cy="16" r="4" />
        <circle cx="32.5" cy="8.5" r="4" />
        <circle cx="32.5" cy="23.5" r="4" />
        <path d="M11.5 16h6.2c3.4 0 3.4-7.5 7.6-7.5h3.2" />
        <path d="M11.5 16h6.2c3.4 0 3.4 7.5 7.6 7.5h3.2" />
      </g>
    </svg>
  );
}

/** Symbol plus lowercase-style wordmark, the way n8n locks up its logo. */
export function Logo({
  className,
  markClassName,
  textClassName,
}: {
  className?: string;
  markClassName?: string;
  textClassName?: string;
}) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <LogoMark className={cn('h-7 w-auto shrink-0', markClassName)} />
      <span className={cn('text-lg font-semibold tracking-tight', textClassName)}>
        OpenAgent
      </span>
    </span>
  );
}
