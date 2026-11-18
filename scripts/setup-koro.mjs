#!/usr/bin/env node
/**
 * Set up Koro's local model: qwen2.5:0.5b-instruct (~400 MB, CPU friendly).
 *
 * Koro talks to any OpenAI-compatible local server. The default is Ollama on
 * http://127.0.0.1:11434/v1. Without a model Koro still runs commands, but the
 * natural-language path needs one.
 */
import { spawnSync } from 'node:child_process';

const MODEL = process.env.OPENAGENT_KORO_MODEL || 'qwen2.5:0.5b-instruct';
const BASE = (process.env.OPENAGENT_KORO_BASE_URL || 'http://127.0.0.1:11434/v1').replace(/\/$/, '');

function run(command, args) {
  return spawnSync(command, args, { stdio: 'inherit', shell: process.platform === 'win32' });
}

console.log(`Koro local model: ${MODEL} (~400 MB)\n`);

const version = spawnSync('ollama', ['--version'], {
  encoding: 'utf8',
  shell: process.platform === 'win32',
});

if (version.status !== 0) {
  console.log('Ollama is not installed.\n');
  console.log('1. Install it from https://ollama.com/download (Windows/macOS/Linux).');
  console.log('2. Re-run: pnpm setup:koro');
  console.log(`\nAny OpenAI-compatible server also works — point OPENAGENT_KORO_BASE_URL at it,`);
  console.log(`but it must serve the model “${MODEL}”.`);
  process.exit(0);
}

console.log(`Ollama found: ${version.stdout.trim()}`);
console.log(`Pulling ${MODEL} (first run downloads roughly 400 MB)…`);

const pull = run('ollama', ['pull', MODEL]);
if (pull.status !== 0) {
  console.error(`\nPull failed. Check your network, then run: ollama pull ${MODEL}`);
  process.exit(1);
}

console.log('\nModel ready. Checking the API endpoint…');

let ready = false;
for (let attempt = 0; attempt < 20 && !ready; attempt += 1) {
  try {
    const response = await fetch(`${BASE}/models`);
    ready = response.ok;
  } catch {
    // Ollama may still be starting.
  }
  if (!ready) await new Promise((resolve) => setTimeout(resolve, 500));
}

if (ready) {
  console.log(`Koro server reachable at ${BASE}`);
} else {
  console.log(`Could not reach ${BASE}. Start Ollama, then Koro will detect it automatically.`);
}

console.log('\nDone. Open a workflow in the builder and press Koro.');
