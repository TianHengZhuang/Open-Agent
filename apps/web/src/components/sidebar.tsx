'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Logo, LogoMark } from '@/components/brand';
import {
  Bot,
  LayoutDashboard,
  GitBranch,
  Play,
  Wrench,
  Plug,
  Brain,
  Code2,
  Container,
  Store,
  Settings,
  ShieldCheck,
  ChevronLeft,
  ChevronRight,
  FileStack,
  BookOpen,
  Server,
  Sparkles,
  SlidersHorizontal,
  Building2,
} from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { useFeature } from '@/lib/feature-flags';

const primaryNav = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard, exact: true },
  { name: 'Agents', href: '/agents', icon: Bot },
  { name: 'Orchestrations', href: '/orchestrations', icon: Play },
  { name: 'Management', href: '/management', icon: ShieldCheck },
  { name: 'Workflows', href: '/workflows', icon: GitBranch },
  { name: 'Runs', href: '/runs', icon: Play },
  { name: 'Tools', href: '/tools', icon: Wrench },
  { name: 'MCP Servers', href: '/mcp', icon: Server },
  { name: 'Integrations', href: '/integrations', icon: Plug },
  { name: 'Memory', href: '/memory', icon: Brain },
  { name: 'Code', href: '/code', icon: Code2 },
  { name: 'Sandbox', href: '/sandbox', icon: Container },
  { name: 'Marketplace', href: '/marketplace', icon: Store },
];

const developerNav = [
  { name: 'Overview', href: '/developer', icon: Code2, exact: true },
  { name: 'Projects', href: '/developer/projects', icon: Building2 },
  { name: 'Extensions', href: '/developer/extensions', icon: Plug },
  { name: 'New Extension', href: '/developer/extensions/new', icon: Sparkles },
  { name: 'SDK', href: '/developer/sdk', icon: Code2 },
  { name: 'CLI', href: '/developer/cli', icon: SlidersHorizontal },
  { name: 'API Explorer', href: '/developer/api', icon: FileStack },
  { name: 'Docs', href: '/developer/docs', icon: BookOpen },
  { name: 'Registry', href: '/developer/registry', icon: Container },
  { name: 'Webhooks & Usage', href: '/developer/settings', icon: Settings },
];

const secondaryNav = [
  { name: 'Templates', href: '/templates', icon: FileStack },
  { name: 'Skills', href: '/skills', icon: Sparkles },
  { name: 'Presets', href: '/presets', icon: SlidersHorizontal },
  { name: 'Publisher Studio', href: '/publisher/packages', icon: Building2 },
  { name: 'Documentation', href: '/docs', icon: BookOpen },
  { name: 'Settings', href: '/settings', icon: Settings },
];

interface SidebarProps {
  collapsed?: boolean;
  onToggle?: () => void;
  mobile?: boolean;
}

export function Sidebar({ collapsed = false, onToggle, mobile = false }: SidebarProps) {
  const pathname = usePathname();
  const { user } = useAuth();
  const marketplaceOn = useFeature('marketplace');
  const isPlatform = !!(user?.is_platform_owner || user?.is_superadmin);

  const isActive = (href: string, exact?: boolean) =>
    exact ? pathname === href : pathname === href || pathname.startsWith(href + '/');

  const renderLink = (item: { name: string; href: string; icon: React.ElementType; exact?: boolean }) => {
    if (item.name === 'Marketplace' && !marketplaceOn) return null;
    const active = isActive(item.href, item.exact);
    return (
      <Link
        key={item.name}
        href={item.href}
        aria-current={active ? 'page' : undefined}
        title={collapsed && !mobile ? item.name : undefined}
        className={cn(
          'group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          active
            ? 'bg-primary/10 text-primary'
            : 'text-muted-foreground hover:bg-accent/70 hover:text-foreground',
          collapsed && !mobile && 'justify-center px-2',
        )}
      >
        {active && !collapsed && (
          <span
            className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary"
            aria-hidden
          />
        )}
        <item.icon className={cn('h-5 w-5 shrink-0', active ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground')} aria-hidden />
        {(!collapsed || mobile) && <span>{item.name}</span>}
      </Link>
    );
  };

  return (
    <aside
      aria-label="Main navigation"
      className={cn(
        'flex h-screen flex-col border-r bg-sidebar transition-all duration-200',
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
        {(!collapsed || mobile) ? (
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

      <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Primary">
        {primaryNav.map(renderLink)}
        {(!collapsed || mobile) && (
          <p className="px-3 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
            Developer
          </p>
        )}
        <div role="group" aria-label="Developer">
          {developerNav.map(renderLink)}
        </div>
        {isPlatform && (
          <Link
            href="/platform"
            aria-current={isActive('/platform') ? 'page' : undefined}
            title={collapsed && !mobile ? 'Platform' : undefined}
            className={cn(
              'mt-1 flex items-center gap-3 rounded-lg border border-dashed px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
              collapsed && !mobile && 'justify-center px-2',
            )}
          >
            <ShieldCheck className="h-5 w-5 shrink-0" aria-hidden />
            {(!collapsed || mobile) && <span>Platform</span>}
          </Link>
        )}
        {isPlatform && (!collapsed || mobile) && (
          <Link
            href="/master/marketplace"
            aria-current={isActive('/master/marketplace') ? 'page' : undefined}
            className="mt-1 flex items-center gap-3 rounded-lg px-3 py-2 pl-11 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <span>Marketplace moderation</span>
          </Link>
        )}
      </nav>

      <nav className="space-y-1 border-t border-sidebar-border p-3" aria-label="Secondary">
        {secondaryNav.map(renderLink)}
      </nav>

      <div className="border-t border-sidebar-border p-3">
        {(!collapsed || mobile) ? (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-background/60 px-2.5 py-1.5">
            <p className="text-[11px] text-muted-foreground">OpenAgent v0.1.0</p>
            <kbd className="oa-kbd">Ctrl K</kbd>
          </div>
        ) : (
          <p className="text-center text-[11px] text-muted-foreground" aria-hidden>v0.1</p>
        )}
      </div>
    </aside>
  );
}
