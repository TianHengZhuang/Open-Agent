'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Logo, LogoMark } from '@/components/brand';
import {
  ChevronLeft,
  ChevronRight,
  GitBranch,
  Play,
  Plus,
  Settings,
  Sparkles,
  UserRound,
} from 'lucide-react';
import { PRIMARY_NAV, type SurfaceIcon } from '@/features/navigation/surfaces';

/**
 * Phase 14: the sidebar is the six surfaces, nothing else. Every former page
 * forwards to the assistant (see `features/navigation/surfaces.ts`), so browse
 * tasks are one message away instead of one more nav entry.
 */
const ICONS: Record<SurfaceIcon, React.ElementType> = {
  chat: Sparkles,
  workflow: GitBranch,
  runs: Play,
  settings: Settings,
  account: UserRound,
};

interface SidebarProps {
  collapsed?: boolean;
  onToggle?: () => void;
  mobile?: boolean;
}

export function Sidebar({ collapsed = false, onToggle, mobile = false }: SidebarProps) {
  const pathname = usePathname();

  const isActive = (href: string, exact?: boolean) =>
    exact ? pathname === href : pathname === href || pathname.startsWith(href + '/');

  const renderLink = (item: (typeof PRIMARY_NAV)[number]) => {
    const active = isActive(item.href, item.exact);
    const Icon = ICONS[item.icon];
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={active ? 'page' : undefined}
        title={collapsed && !mobile ? item.label : undefined}
        className={cn(
          'group relative flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          active
            ? 'bg-primary/10 text-primary'
            : 'text-muted-foreground hover:bg-accent/70 hover:text-foreground',
          collapsed && !mobile && 'justify-center px-2',
        )}
      >
        {active && !collapsed && (
          <span
            className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-primary"
            aria-hidden
          />
        )}
        <Icon
          className={cn(
            'h-[18px] w-[18px] shrink-0',
            active ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground',
          )}
          aria-hidden
        />
        {(!collapsed || mobile) && <span className="truncate">{item.label}</span>}
      </Link>
    );
  };

  return (
    <aside
      aria-label="Main navigation"
      className={cn(
        'flex h-screen flex-col border-r border-sidebar-border bg-sidebar transition-all duration-200',
        mobile ? 'w-64' : collapsed ? 'w-16' : 'w-64',
        !mobile && 'fixed left-0 top-0 z-40 hidden lg:flex',
        mobile && 'fixed left-0 top-0 z-50 lg:hidden',
      )}
    >
      <div
        className={cn(
          'flex h-16 items-center border-b border-sidebar-border',
          collapsed && !mobile
            ? 'flex-col justify-center gap-0.5 px-2'
            : 'justify-between gap-1 px-4',
        )}
      >
        {!collapsed || mobile ? (
          <Link href="/" className="min-w-0" aria-label="OpenAgent home">
            <Logo />
          </Link>
        ) : (
          <Link href="/" className="flex items-center justify-center" aria-label="OpenAgent home">
            <LogoMark className="h-4 w-auto" />
          </Link>
        )}
        {onToggle && (
          <button
            onClick={onToggle}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!collapsed}
            className="shrink-0 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        )}
      </div>

      {(!collapsed || mobile) && (
        <div className="px-3 pt-3">
          <Link
            href="/workflows/new"
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-[13px] font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
          >
            <Plus className="h-4 w-4" aria-hidden />
            New workflow
          </Link>
        </div>
      )}

      <nav className="oa-scroll flex-1 space-y-0.5 overflow-y-auto p-3" aria-label="Primary">
        {PRIMARY_NAV.map((item) => renderLink(item))}
      </nav>

      <div className="border-t border-sidebar-border p-3">
        {!collapsed || mobile ? (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-background/60 px-2.5 py-1.5">
            <p className="text-[11px] text-muted-foreground">OpenAgent v0.1.0</p>
            <kbd className="oa-kbd">Ctrl K</kbd>
          </div>
        ) : (
          <p className="text-center text-[11px] text-muted-foreground" aria-hidden>
            v0.1
          </p>
        )}
      </div>
    </aside>
  );
}
