// Context the chatbox sends with a turn, plus the prompt used by
// "Ask Koro to fix" on a failed step. Pure helpers so they stay testable.

export type KoroStep = { id: string; name: string; type: string };

export interface KoroScope {
  route?: string;
  workflowId?: string;
  workflowName?: string;
  nodeCount?: number;
  edgeCount?: number;
  /** Node outline (id/name/type only — never parameters or credentials). */
  steps?: KoroStep[];
  validation?: { errors: number; warnings: number };
  lastRun?: { status: string; failed_nodes: string[] };
}

export interface KoroRequestContext {
  route?: string;
  workflow?: {
    id: string;
    name: string;
    nodes: number;
    edges: number;
    steps?: KoroStep[];
  };
  validation?: { errors: number; warnings: number };
  last_run?: { status: string; failed_nodes: string[] };
  /** Build applies changes; plan only explains and proposes. */
  mode?: 'build' | 'plan';
}

/** Trim undefined fields so the backend gets a compact, explicit context. */
export function buildKoroContext(
  scope: KoroScope | null,
  pathname?: string,
): KoroRequestContext {
  const context: KoroRequestContext = {};
  const route = scope?.route ?? pathname;
  if (route) context.route = route;
  if (scope?.workflowId) {
    context.workflow = {
      id: scope.workflowId,
      name: scope.workflowName ?? 'Untitled workflow',
      nodes: scope.nodeCount ?? 0,
      edges: scope.edgeCount ?? 0,
    };
    if (scope.steps && scope.steps.length > 0) {
      context.workflow.steps = scope.steps.slice(0, 30);
    }
  }
  if (scope?.validation) context.validation = scope.validation;
  if (scope?.lastRun) context.last_run = scope.lastRun;
  return context;
}

function clip(value: unknown, limit = 900): string {
  if (value === undefined || value === null) return '';
  let text: string;
  try {
    text = JSON.stringify(value);
  } catch {
    return '';
  }
  if (!text || text === '{}' || text === 'null') return '';
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

export interface FailedStepLike {
  node_id: string;
  node_name: string;
  node_type: string;
  error?: string | null;
  error_code?: string | null;
  inputs?: Record<string, unknown> | null;
  outputs?: Record<string, unknown>;
}

/** A concrete, self-contained debugging prompt for the copilot. */
export function buildFixPrompt(
  run: FailedStepLike,
  workflow: { id: string; name: string },
): string {
  const lines = [
    `Help me fix a failed step in “${workflow.name}” (workflow id ${workflow.id}).`,
    `Step: “${run.node_name}” [${run.node_type}] (node id ${run.node_id}).`,
    `Error: ${run.error ?? 'unknown error'}${run.error_code ? ` (${run.error_code})` : ''}.`,
  ];
  const input = clip(run.inputs);
  if (input) lines.push(`Input: ${input}`);
  const output = clip(run.outputs);
  if (output) lines.push(`Output: ${output}`);
  lines.push(
    'Explain the root cause in plain language, then give the exact node setting or expression change that fixes it.',
  );
  return lines.join('\n');
}
