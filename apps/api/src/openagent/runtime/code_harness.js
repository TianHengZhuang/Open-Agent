/**
 * Code-node harness for JavaScript, executed once per run by
 * `openagent.runtime.code_runner` in a short-lived `node` process.
 *
 * Payload on stdin, result JSON written to `argv[2]`:
 *   {items: [{json, binary}], code, mode: "all"|"each", parameters}
 *
 * The vm context exposes only data globals (`$input`, `$json`, `$helpers`,
 * `items`, `parameters`) — no require/process/console. Node itself is started
 * with `--disallow-code-generation-from-strings`, so `Function`/`eval`
 * escapes fail. Production still runs this inside the Docker backend.
 */

'use strict';

const fs = require('fs');
const vm = require('vm');

const MAX_STDOUT = 64_000;

function readStdin() {
  return fs.readFileSync(0, 'utf8');
}

function normalize(value) {
  if (value === undefined || value === null) {
    throw new Error('Code must return an object or a list of objects (returned nothing)');
  }
  const asItem = (entry) => {
    if (entry && typeof entry === 'object' && 'json' in entry
      && Object.keys(entry).every((key) => ['json', 'binary', 'pairedItem'].includes(key))) {
      return entry;
    }
    return { json: entry };
  };
  if (Array.isArray(value)) return value.map(asItem);
  return [asItem(value)];
}

function contextFor(items, current, parameters) {
  const input = {
    all: () => items.slice(),
    first: () => items[0],
    last: () => items[items.length - 1],
    item: current,
  };
  const helpers = {
    jsonStringify: (value) => JSON.stringify(value),
    jsonParse: (text) => JSON.parse(text),
  };
  return {
    $input: input,
    $json: current ? current.json : {},
    $helpers: helpers,
    $parameter: parameters,
    parameters,
    items,
  };
}

const resultPath = process.argv[2];
const payload = JSON.parse(readStdin());
const stdout = [];
let stdoutSize = 0;
let stderr = '';

function capture(text) {
  if (stdoutSize >= MAX_STDOUT) return;
  const chunk = String(text);
  const room = MAX_STDOUT - stdoutSize;
  stdout.push(chunk.slice(0, room));
  stdoutSize += Math.min(chunk.length, room);
}

const rawItems = Array.isArray(payload.items) ? payload.items : [];
const items = rawItems.map((entry) => ({
  json: entry && typeof entry === 'object' && 'json' in entry ? entry.json : entry,
  binary: entry && typeof entry === 'object' ? entry.binary || {} : {},
}));
const mode = String(payload.mode || 'all').toLowerCase();
const parameters = payload.parameters || {};
const wrapped = `(function () {\n${String(payload.code || '')}\n})()`;

const sandbox = {
  JSON, Math, Date, String, Number, Boolean, Array, Object, RegExp, Error,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
};
vm.createContext(sandbox);

function runFor(current) {
  Object.assign(sandbox, contextFor(items, current, parameters));
  const value = vm.runInContext(wrapped, sandbox, {
    timeout: Number(payload.timeout_seconds || 30) * 1000,
    breakOnSigint: true,
  });
  return normalize(value);
}

let result;
try {
  let output = [];
  if (mode === 'each') {
    for (const entry of items) output = output.concat(runFor(entry));
  } else {
    output = runFor(items[0] || { json: {}, binary: {} });
  }
  const encoded = JSON.stringify({ items: output });
  const maxOutput = Number(payload.max_output_bytes || 1000000);
  if (Buffer.byteLength(encoded, 'utf8') > maxOutput) {
    result = {
      error: {
        code: 'CODE_OUTPUT_TOO_LARGE',
        message: `Code output exceeds ${maxOutput} bytes`,
      },
    };
  } else {
    result = JSON.parse(encoded);
  }
} catch (error) {
  result = {
    error: {
      code: 'CODE_EXECUTION_ERROR',
      message: String((error && error.message) || error).slice(0, 2000),
    },
    stderr: String((error && error.stack) || '').slice(0, 4000),
  };
  stderr = result.stderr || '';
}

result.stdout = stdout.join('') + (stdoutSize >= MAX_STDOUT ? '\n[output truncated]' : '');
if (!result.stderr) result.stderr = stderr;
fs.writeFileSync(resultPath, JSON.stringify(result));
