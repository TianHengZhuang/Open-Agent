'use client';

import { ReactNode } from 'react';
import Link from 'next/link';
import { Logo } from '@/components/brand';

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-background">
      <div className="hidden w-1/2 flex-col justify-between bg-sidebar p-10 lg:flex">
        <Link href="/" aria-label="OpenAgent home">
          <Logo />
        </Link>
        <div className="space-y-4">
          <h1 className="oa-display">The AI Workforce Operating System</h1>
          <p className="max-w-md text-muted-foreground">
            Agents, workflows, executions, tools, and integrations — one control plane for your AI workforce.
          </p>
        </div>
        <p className="text-xs text-muted-foreground">Secure session cookies · RBAC · Audit-ready</p>
      </div>
      <div className="flex w-full items-center justify-center p-6 lg:w-1/2">
        <div className="w-full max-w-md">{children}</div>
      </div>
    </div>
  );
}
