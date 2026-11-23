// Client-side expression evaluation for previews and tests. Expressions are
// author-supplied workflow config, evaluated in a scoped function whose globals
// (window, fetch, process, …) are shadowed, with a length cap. This is a
// convenience sandbox for the editor, not a security boundary for untrusted
// code — the backend runtime stays the source of truth for real executions.

export interface EngineItem {
  json: Record<string, unknown>;
  binary?: Record<string, unknown>;
  pairedItem?: number | number[];
  error?: { message?: string };
}

export interface EvalContext {
  /** The current item. */
  json?: Record<string, unknown>;
  /** Items reaching this node (for `$input`). */
  items?: EngineItem[];
  /** Outputs keyed by node name (for `$node["Name"]`). */
  nodeOutputs?: Record<string, unknown>;
  vars?: Record<string, unknown>;
  execution?: { id?: string; mode?: string };
  workflow?: { id?: string; name?: string };
  now?: Date;
}

export class ExpressionError extends Error {
  readonly expression: string;

  constructor(message: string, expression: string) {
    super(message);
    this.name = 'ExpressionError';
    this.expression = expression;
  }
}

const MAX_EXPRESSION_LENGTH = 2000;
const BLOCKED_GLOBALS = [
  'window',
  'document',
  'globalThis',
  'self',
  'fetch',
  'XMLHttpRequest',
  'WebSocket',
  'localStorage',
  'sessionStorage',
  'process',
  'require',
  'module',
  'exports',
  'Function',
  'WebAssembly',
];

function itemView(item: EngineItem) {
  return {
    json: item.json ?? {},
    binary: item.binary,
    pairedItem: item.pairedItem,
  };
}

/** n8n-style `$input` helper object. */
export function makeInput(items: EngineItem[]) {
  return {
    first: () => itemView(items[0] ?? { json: {} }),
    last: () => itemView(items[items.length - 1] ?? { json: {} }),
    all: () => items.map(itemView),
    item: itemView(items[0] ?? { json: {} }),
  };
}

/** `$node["Name"].json` lookups; unknown nodes read as `{}`. */
export function makeNodeLookup(outputs: Record<string, unknown> = {}) {
  return new Proxy(
    {},
    {
      get: (_target, name: string) => {
        const value = outputs[name];
        if (Array.isArray(value)) {
          return { json: (value[0] as EngineItem)?.json ?? value[0] ?? {}, items: value };
        }
        if (value && typeof value === 'object' && 'json' in (value as object)) return value;
        return { json: (value as Record<string, unknown>) ?? {} };
      },
    },
  );
}

/** Evaluate one expression body (the text between `{{` and `}}`). */
export function evaluateExpression(body: string, ctx: EvalContext = {}): unknown {
  const code = body.trim();
  if (!code) return undefined;
  if (code.length > MAX_EXPRESSION_LENGTH) {
    throw new ExpressionError('Expression is too long', body);
  }

  const scope: Record<string, unknown> = {
    $json: ctx.json ?? {},
    $input: makeInput(ctx.items ?? []),
    $node: makeNodeLookup(ctx.nodeOutputs),
    $vars: ctx.vars ?? {},
    $execution: { id: ctx.execution?.id ?? 'preview', mode: ctx.execution?.mode ?? 'preview' },
    $workflow: {
      id: ctx.workflow?.id ?? 'preview',
      name: ctx.workflow?.name ?? 'Preview',
    },
    $now: ctx.now ?? new Date(),
  };

  const names = Object.keys(scope);
  const blocked = BLOCKED_GLOBALS;
  let fn: (...args: unknown[]) => unknown;
  try {
    // eslint-disable-next-line no-new-func
    fn = new Function(
      ...blocked,
      ...names,
      `"use strict"; return (${code});`,
    ) as (...args: unknown[]) => unknown;
  } catch (error) {
    throw new ExpressionError(
      error instanceof Error ? error.message : 'Could not parse expression',
      body,
    );
  }

  try {
    return fn(...blocked.map(() => undefined), ...names.map((name) => scope[name]));
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Expression failed';
    throw new ExpressionError(message, body);
  }
}

const SEGMENT = /\{\{([\s\S]*?)\}\}/g;

/**
 * Resolve a config value: a lone `{{ … }}` (or n8n's `={{ … }}`) keeps the
 * expression's type; mixed text interpolates as a string. Strings without
 * expressions pass through untouched.
 */
export function resolveValue(value: unknown, ctx: EvalContext = {}): unknown {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    const single = /^=?\{\{([\s\S]*)\}\}$/.exec(trimmed);
    if (single && !/\}\}/.test(single[1])) {
      return evaluateExpression(single[1], ctx);
    }
    if (!trimmed.includes('{{')) return value;
    return value.replace(SEGMENT, (_match, body: string) => {
      const result = evaluateExpression(body, ctx);
      if (result === undefined || result === null) return '';
      return typeof result === 'object' ? JSON.stringify(result) : String(result);
    });
  }
  if (Array.isArray(value)) return value.map((entry) => resolveValue(entry, ctx));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, entry]) => [
        key,
        resolveValue(entry, ctx),
      ]),
    );
  }
  return value;
}

/** Resolve a condition expression to a boolean, treating errors as false. */
export function evaluateCondition(body: string, ctx: EvalContext = {}): boolean {
  try {
    return Boolean(evaluateExpression(body, ctx));
  } catch {
    return false;
  }
}

/** Authoring check: does this string contain an expression? */
export function hasExpression(value: unknown): boolean {
  return typeof value === 'string' && /\{\{\s*[\s\S]*?\}\}/.test(value);
}
