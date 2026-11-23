// Paste a cURL command, get an HTTP Request node. No model, no network — the
// command is parsed locally and returned as a typed graph mutation.

import type { GraphMutation } from './mutations';

export interface CurlRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body?: string;
}

export type HttpNodeMutation = Extract<GraphMutation, { op: 'add_node' }>;

const FLAGS_WITH_VALUE = new Set([
  '--url',
  '-X',
  '--request',
  '-H',
  '--header',
  '-d',
  '--data',
  '--data-raw',
  '--data-ascii',
  '--data-binary',
  '--data-urlencode',
  '-b',
  '--cookie',
  '-u',
  '--user',
]);

/** Split a shell-ish command, honouring quotes and line continuations. */
function tokenize(command: string): string[] {
  const text = command.replace(/\\\r?\n/g, ' ').trim();
  const tokens: string[] = [];
  let current = '';
  let quote: '"' | "'" | null = null;
  let started = false;

  for (const char of text) {
    if (quote) {
      if (char === quote) quote = null;
      else current += char;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      started = true;
      continue;
    }
    if (/\s/.test(char)) {
      if (started) tokens.push(current);
      current = '';
      started = false;
      continue;
    }
    current += char;
    started = true;
  }
  if (started) tokens.push(current);
  return tokens;
}

export function parseCurl(command: string): CurlRequest | null {
  const tokens = tokenize(command);
  if (tokens.length === 0 || tokens[0].replace(/^\$/, '') !== 'curl') return null;

  let method = '';
  let url = '';
  let body: string | undefined;
  const headers: Record<string, string> = {};

  for (let index = 1; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === '--') {
      url = tokens[index + 1] ?? url;
      break;
    }
    if (token.startsWith('-')) {
      const flag = token.split('=')[0];
      const inlineValue = token.includes('=') ? token.slice(token.indexOf('=') + 1) : undefined;
      if (!FLAGS_WITH_VALUE.has(flag)) continue;
      const value = inlineValue ?? tokens[index + 1] ?? '';
      if (inlineValue === undefined) index += 1;

      switch (flag) {
        case '-X':
        case '--request':
          method = value.toUpperCase();
          break;
        case '--url':
          url = value;
          break;
        case '-H':
        case '--header': {
          const separator = value.indexOf(':');
          if (separator > 0) {
            headers[value.slice(0, separator).trim()] = value.slice(separator + 1).trim();
          }
          break;
        }
        case '-d':
        case '--data':
        case '--data-raw':
        case '--data-ascii':
        case '--data-binary':
        case '--data-urlencode':
          body = body ? `${body}&${value}` : value;
          break;
        case '-b':
        case '--cookie':
          headers.Cookie = value;
          break;
        case '-u':
        case '--user': {
          if (typeof btoa === 'function') {
            headers.Authorization = `Basic ${btoa(value)}`;
          }
          break;
        }
        default:
          break;
      }
      continue;
    }
    if (!url) url = token;
  }

  if (!url) return null;
  return {
    url,
    method: method || (body !== undefined ? 'POST' : 'GET'),
    headers,
    ...(body !== undefined ? { body } : {}),
  };
}

function labelFor(request: CurlRequest): string {
  try {
    const host = new URL(request.url).hostname;
    return `HTTP ${request.method} ${host}`;
  } catch {
    return `HTTP ${request.method}`;
  }
}

/** Parse a cURL command into an `add_node` mutation for an HTTP Request node. */
export function curlToMutation(
  command: string,
  options: { id?: string; position?: { x: number; y: number } } = {},
): HttpNodeMutation | null {
  const request = parseCurl(command);
  if (!request) return null;
  return {
    op: 'add_node',
    ...(options.id ? { id: options.id } : {}),
    ...(options.position ? { position: options.position } : {}),
    type: 'webhook',
    label: labelFor(request),
    config: {
      url: request.url,
      method: request.method,
      ...(Object.keys(request.headers).length > 0 ? { headers: request.headers } : {}),
      ...(request.body !== undefined ? { body: request.body } : {}),
    },
  };
}
