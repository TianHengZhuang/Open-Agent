import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import { LEGACY_REDIRECTS, PRIMARY_NAV, legacyTarget } from './surfaces';

const WEB_ROOT = process.cwd();
const APP_DIR = join(WEB_ROOT, 'src/app');
const SIDEBAR = join(WEB_ROOT, 'src/components/sidebar.tsx');

/** Routes that keep a real page (Phase 14 target surfaces). */
const KEPT = new Set([
  '/login', '/register', '/forgot-password', '/reset-password', '/verify-email',
  '/account', '/assistant',
  '/workflows', '/workflows/new', '/workflows/[id]', '/workflows/[id]/edit',
  '/runs', '/runs/[id]',
  '/settings', '/settings/billing', '/settings/billing/[id]',
  '/settings/enterprise', '/settings/enterprise/controls', '/settings/enterprise/devices',
  '/settings/enterprise/mfa', '/settings/enterprise/policies', '/settings/enterprise/scim',
  '/settings/enterprise/sso', '/settings/models', '/settings/packages',
  '/settings/registries', '/settings/usage',
  '/master/billing',
]);

function pageFiles(dir: string = APP_DIR): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) files.push(...pageFiles(full));
    else if (entry === 'page.tsx') files.push(full);
  }
  return files;
}

function routeOf(file: string): string {
  const relDir = relative(APP_DIR, dirname(file)).split(sep).join('/');
  const cleaned = relDir.replace(/\([^)]+\)\/?/g, '').replace(/^\/+|\/+$/g, '');
  return cleaned ? `/${cleaned}` : '/';
}

describe('Phase 14 surfaces', () => {
  const pages = pageFiles();
  const routes = new Map(pages.map((file) => [routeOf(file), file]));

  it('keeps the shipped route count collapsed', () => {
    expect(pages.length).toBe(114);
    expect(KEPT.size).toBe(28);
    expect(KEPT.size + Object.keys(LEGACY_REDIRECTS).length).toBe(pages.length);
  });

  it('keeps exactly the target surfaces as real pages', () => {
    for (const route of KEPT) {
      expect(routes.has(route), `${route} should still have a page`).toBe(true);
      expect(LEGACY_REDIRECTS[route], `${route} must not redirect`).toBeUndefined();
    }
  });

  it('forwards every other former page to the assistant', () => {
    for (const [route, file] of routes) {
      if (KEPT.has(route)) continue;
      expect(LEGACY_REDIRECTS[route], `${route} needs a legacy redirect`).toBeDefined();
      const source = readFileSync(file, 'utf8');
      expect(source, `${route} must use legacyTarget`).toContain(`legacyTarget('${route}')`);
      expect(source).toContain("from 'next/navigation'");
    }
  });

  it('declares no redirect for a page that no longer exists', () => {
    for (const route of Object.keys(LEGACY_REDIRECTS)) {
      expect(routes.has(route), `${route} has no page.tsx`).toBe(true);
    }
  });

  it('encodes the intent into the assistant prompt', () => {
    const entry = LEGACY_REDIRECTS['/agents'];
    expect(entry).toBeDefined();
    expect(legacyTarget('/agents')).toBe(
      `/assistant?prompt=${encodeURIComponent(entry.prompt)}`,
    );
    expect(legacyTarget('/')).toContain('/assistant?prompt=');
  });

  it('refuses to guess an unknown route', () => {
    expect(() => legacyTarget('/definitely-not-a-page')).toThrow(/No legacy redirect/);
  });

  it('has a six-surface sidebar driven by PRIMARY_NAV', () => {
    expect(PRIMARY_NAV.length).toBeLessThanOrEqual(7);
    const hrefs = PRIMARY_NAV.map((item) => item.href);
    expect(hrefs).toEqual(['/assistant', '/workflows', '/runs', '/settings', '/account']);
    expect(new Set(hrefs).size).toBe(hrefs.length);
    for (const href of hrefs) expect(href.startsWith('/')).toBe(true);

    const sidebar = readFileSync(SIDEBAR, 'utf8');
    expect(sidebar).toContain('PRIMARY_NAV');
    for (const legacy of ['/agents', '/mcp', '/marketplace', '/developer', '/operations']) {
      expect(sidebar, `sidebar should not link ${legacy}`).not.toContain(`'${legacy}'`);
    }
  });
});
