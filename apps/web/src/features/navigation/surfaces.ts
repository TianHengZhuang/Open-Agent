// Navigation surfaces and legacy-route forwarding (Phase 14).
//
// The product collapses to six surfaces: chat, workflows, canvas, runs,
// settings and account. Every other former page forwards to the assistant
// with the intent it used to serve, so no capability is lost.

export type SurfaceIcon =
  | 'chat' | 'workflow' | 'runs' | 'settings' | 'account';

export interface PrimarySurface {
  href: string;
  label: string;
  icon: SurfaceIcon;
  exact?: boolean;
}

/** Sidebar entries. Keep this at seven or fewer (Phase 14 acceptance). */
export const PRIMARY_NAV: PrimarySurface[] = [
  { href: '/assistant', label: 'Chat', icon: 'chat', exact: true },
  { href: '/workflows', label: 'Workflows', icon: 'workflow' },
  { href: '/runs', label: 'Executions', icon: 'runs' },
  { href: '/settings', label: 'Settings', icon: 'settings' },
  { href: '/account', label: 'Account', icon: 'account' },
];

export interface LegacyRedirect {
  /** Prompt pre-filled in the assistant for this former page. */
  prompt: string;
  /** Override the destination (defaults to /assistant). */
  to?: string;
}

/** Former page routes and the chat intent that replaces them. */
export const LEGACY_REDIRECTS: Record<string, LegacyRedirect> = {
  '/': { prompt: 'Show me a summary of my workspace and what needs attention' },
  '/agents': { prompt: 'Show my agents and help me create or update one' },
  '/agents/organization': { prompt: 'Show the organization\'s agents and their status' },
  '/approvals': { prompt: 'Show pending approvals and let me approve or reject one' },
  '/approvals/[id]': { prompt: 'Show pending approvals and let me approve or reject one' },
  '/browser': { prompt: 'Open the browser workspace' },
  '/browser/playground': { prompt: 'Open the browser playground' },
  '/chat/[workflowId]': { prompt: 'Continue assisting me with this workflow' },
  '/cloud': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/executions': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/health': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/queues': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/regions': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/settings': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/storage': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/cloud/workers': { prompt: 'Show my cloud runtime: workers, queues, storage, regions and health' },
  '/code': { prompt: 'Open the code workspace' },
  '/code/playground': { prompt: 'Open the code workspace' },
  '/developer': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/api': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/cli': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/docs': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/extensions': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/extensions/[id]': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/extensions/new': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/marketplace': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/projects': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/projects/[id]': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/registry': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/sdk': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/developer/settings': { prompt: 'Open the developer workspace: projects, extensions, SDK, CLI and API' },
  '/docs': { prompt: 'Show documentation and how to use the product' },
  '/integrations': { prompt: 'Show my integrations, credentials and webhooks' },
  '/integrations/[id]': { prompt: 'Show my integrations, credentials and webhooks' },
  '/integrations/builder': { prompt: 'Show my integrations, credentials and webhooks' },
  '/integrations/catalog': { prompt: 'Show my integrations, credentials and webhooks' },
  '/integrations/connected/[id]': { prompt: 'Show my integrations, credentials and webhooks' },
  '/integrations/webhooks': { prompt: 'Show my integrations, credentials and webhooks' },
  '/management': { prompt: 'Show management tasks and escalations' },
  '/management/escalations': { prompt: 'Show management tasks and escalations' },
  '/marketplace': { prompt: 'Search the marketplace for templates and packages' },
  '/marketplace/[slug]': { prompt: 'Search the marketplace for templates and packages' },
  '/marketplace/category/[slug]': { prompt: 'Search the marketplace for templates and packages' },
  '/marketplace/publisher/[slug]': { prompt: 'Search the marketplace for templates and packages' },
  '/marketplace/search': { prompt: 'Search the marketplace for templates and packages' },
  '/master/command': { prompt: 'Open platform administration' },
  '/master/marketplace': { prompt: 'Open platform administration' },
  '/mcp': { prompt: 'Manage my MCP servers and their tools' },
  '/mcp/[id]': { prompt: 'Manage my MCP servers and their tools' },
  '/mcp/install': { prompt: 'Manage my MCP servers and their tools' },
  '/memory': { prompt: 'Search my memory' },
  '/operations': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/alerts': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/audit': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/deployments': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/diagnostics': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/health': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/incidents': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/operations/incidents/[id]': { prompt: 'Show operations health, incidents, alerts and deployments' },
  '/orchestrations': { prompt: 'Show my orchestrations' },
  '/orchestrations/[id]': { prompt: 'Show my orchestrations' },
  '/platform': { prompt: 'Open platform administration' },
  '/presets': { prompt: 'Show my presets' },
  '/publisher/analytics': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/listings': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/packages': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/packages/[id]': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/packages/new': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/products': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/products/[id]/access': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/products/[id]/pricing': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/revenue': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/reviews': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/publisher/security': { prompt: 'Open Publisher Studio: packages, products, revenue and reviews' },
  '/quality': { prompt: 'Show quality evaluations, benchmarks and gates' },
  '/quality/benchmarks': { prompt: 'Show quality evaluations, benchmarks and gates' },
  '/quality/evaluations': { prompt: 'Show quality evaluations, benchmarks and gates' },
  '/quality/evaluations/[id]': { prompt: 'Show quality evaluations, benchmarks and gates' },
  '/quality/gates': { prompt: 'Show quality evaluations, benchmarks and gates' },
  '/sandbox': { prompt: 'Open the sandbox workspace' },
  '/sandbox/playground': { prompt: 'Open the sandbox workspace' },
  '/skills': { prompt: 'Show my skills' },
  '/skills/[id]': { prompt: 'Show my skills' },
  '/templates': { prompt: 'Search templates for a use case' },
  '/templates/[slug]': { prompt: 'Search templates for a use case' },
  '/tools': { prompt: 'Show my tools' },
};

/** Resolved URL for a former page route. Throws for unknown routes. */
export function legacyTarget(route: string): string {
  const entry = LEGACY_REDIRECTS[route];
  if (!entry) {
    throw new Error(`No legacy redirect declared for ${route}`);
  }
  if (entry.to) return entry.to;
  return `/assistant?prompt=${encodeURIComponent(entry.prompt)}`;
}
