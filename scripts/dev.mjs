#!/usr/bin/env node
/*
 * OpenAgent local development orchestrator (cross-platform).
 *
 * Starts the browser-first stack with one command:
 *
 *     pnpm dev
 *     -> Web  (Next.js)       http://localhost:3000
 *     -> API  (FastAPI)       http://localhost:8000
 *
 * No Docker, no PostgreSQL, no Redis and no Electron are required. The API
 * defaults to an embedded SQLite database (zero-dependency local mode). Set
 * `--db=postgres` (or OPENAGENT_DB=postgres) to use the PostgreSQL/Redis
 * stack from .env instead.
 *
 * Usage:
 *   node scripts/dev.mjs [--only=web,api,worker] [--db=sqlite|postgres] [--no-banner]
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const onlyArg = process.argv.find((a) => a.startsWith("--only="));
const dbArg = process.argv.find((a) => a.startsWith("--db="));
const NO_BANNER = process.argv.includes("--no-banner");

const ONLY = onlyArg ? onlyArg.split("=")[1].split(",").map((s) => s.trim()) : ["web", "api"];
const DB = (dbArg ? dbArg.split("=")[1] : process.env.OPENAGENT_DB || "sqlite").toLowerCase();

function loadEnvFile() {
  const vars = {};
  const envPath = path.join(ROOT, ".env");
  try {
    for (const line of readFileSync(envPath, "utf8").split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq > 0) vars[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
    }
  } catch {
    /* defaults below */
  }
  return vars;
}

const fileEnv = loadEnvFile();

const portOf = (url, fallback) => {
  try {
    const p = new URL(url).port;
    return p ? Number(p) : fallback;
  } catch {
    return fallback;
  }
};
const WEB_PORT = portOf(fileEnv.WEB_URL || "http://localhost:3000", 3000);
const API_PORT = portOf(fileEnv.API_URL || "http://localhost:8000", 8000);
const API_ORIGIN = `http://localhost:${API_PORT}`;
const API_BASE = `${API_ORIGIN}/api/v1`;

function apiPython() {
  const candidates = [
    path.join(ROOT, "apps", "api", ".venv", "Scripts", "python.exe"),
    path.join(ROOT, "apps", "api", ".venv", "bin", "python"),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function prefix(tag) {
  const colors = { web: "\x1b[36m", api: "\x1b[32m", worker: "\x1b[35m" };
  const reset = "\x1b[0m";
  const useColor = process.stdout.isTTY && !process.env.NO_COLOR;
  const label = `[${tag}]`;
  return (line) => `${useColor ? (colors[tag] ?? "") + label + reset : label} ${line}`;
}

function probeBind(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", (err) => resolve(err.code === "EADDRINUSE" ? "busy" : "free"));
    server.once("listening", () => server.close(() => resolve("free")));
    if (host) server.listen(port, host);
    else server.listen(port);
  });
}

function probeConnect(port, host) {
  return new Promise((resolve) => {
    const sock = net.connect({ port, host });
    sock.setTimeout(500);
    sock.once("connect", () => {
      sock.destroy();
      resolve(true);
    });
    sock.once("timeout", () => {
      sock.destroy();
      resolve(false);
    });
    sock.once("error", () => resolve(false));
  });
}

// A port is busy if anything is listening on it, across IPv4, IPv6 and the
// wildcard bind (Next.js binds `::`, uvicorn binds `0.0.0.0`). Checking only
// 127.0.0.1 missed those and let a second stack start.
//
// Probes run SEQUENTIALLY: the bind probes open (and close) a temporary
// listener, and a concurrent connect probe would otherwise connect to that
// same temporary listener and report a free port as busy.
async function portBusy(port) {
  if ((await probeBind(port, "0.0.0.0")) === "busy") return true;
  if ((await probeBind(port, "::")) === "busy") return true;
  // Detect listeners bound only to a specific address (e.g. 127.0.0.1) that
  // the wildcard bind probes might not catch on every platform.
  return probeConnect(port, "127.0.0.1");
}

const children = [];
let shuttingDown = false;

function startService(tag, cmd, cmdArgs, cwd, extraEnv = {}) {
  const emit = prefix(tag);
  let child;
  try {
    child = spawn(cmd, cmdArgs, {
      cwd,
      // Root .env first, then real environment (real env wins), then explicit overrides.
      env: { ...fileEnv, ...process.env, ...extraEnv, FORCE_COLOR: "1" },
      stdio: ["ignore", "pipe", "pipe"],
      // Only package managers need the Windows shell (pnpm/npm resolve to
      // *.cmd). Direct executables are spawned without it so paths that
      // contain spaces (very common on Windows) are not word-split.
      shell: process.platform === "win32" && /^(pnpm|npm|npx)$/.test(cmd),
    });
  } catch (err) {
    console.error(emit(`failed to start: ${err.message}`));
    process.exitCode = 1;
    return;
  }
  children.push({ tag, child });
  child.stdout.on("data", (d) => {
    for (const line of String(d).split("\n")) {
      if (line.trim()) console.log(emit(line));
    }
  });
  child.stderr.on("data", (d) => {
    for (const line of String(d).split("\n")) {
      if (line.trim()) console.error(emit(line));
    }
  });
  child.on("exit", (code, signal) => {
    const msg = emit(`exited (code=${code ?? "?"}, signal=${signal ?? "?"})`);
    if (shuttingDown) console.log(msg);
    else console.error(msg);
  });
  console.log(emit(`started (pid ${child.pid})`));
}

function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`\nStopping OpenAgent (${signal})…`);
  for (const { child } of children) {
    try {
      child.kill("SIGTERM");
    } catch {
      /* already gone */
    }
  }
  const deadline = setTimeout(() => {
    for (const { child } of children) {
      try {
        child.kill("SIGKILL");
      } catch {
        /* already gone */
      }
    }
    process.exit(0);
  }, 8000);
  deadline.unref?.();
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

async function main() {
  if (!NO_BANNER) {
    console.log(
      [
        "",
        "╔══════════════════════════════════════════════╗",
        "║                 OpenAgent                    ║",
        "║         Open-Source AI Workforce OS          ║",
        "╚══════════════════════════════════════════════╝",
        "",
        `Web:      http://localhost:${WEB_PORT}`,
        `API:      ${API_ORIGIN}`,
        `API Docs: ${API_ORIGIN}/docs`,
        `Health:   ${API_BASE}/health`,
        "",
        "Environment: development (local processes)",
        "Press Ctrl+C to stop.",
        "",
      ].join("\n"),
    );
  }

  const webBusy = await portBusy(WEB_PORT);
  const apiBusy = await portBusy(API_PORT);
  if (ONLY.includes("web") && webBusy) {
    console.error(`Port ${WEB_PORT} is already in use. Stop the process using it (or set WEB_URL) and retry.`);
  }
  if (ONLY.includes("api") && apiBusy) {
    console.error(`Port ${API_PORT} is already in use. Stop the process using it (or set API_URL) and retry.`);
  }
  if ((ONLY.includes("web") && webBusy) || (ONLY.includes("api") && apiBusy)) {
    process.exit(1);
  }

  if (ONLY.includes("web")) {
    startService("web", "pnpm", ["--filter", "@openagent/web", "dev"], ROOT, {
      NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || API_BASE,
    });
  }

  if (ONLY.includes("api")) {
    const python = apiPython();
    if (python === "python" || python === "python3") {
      console.warn(
        `[api] No apps/api/.venv found — using "${python}" from PATH. ` +
          "Create the environment with: cd apps/api && python -m venv .venv && " +
          ".venv/Scripts/pip install -e \".[dev]\"  (or ../../scripts/setup.*)",
      );
    }

    // Zero-dependency local database default. `--db=postgres` uses the
    // DATABASE_URL/REDIS_URL from .env (needs Docker or local servers).
    const apiEnv = { PYTHONPATH: path.join(ROOT, "apps", "api", "src") };
    if (DB === "sqlite") {
      apiEnv.DATABASE_URL =
        process.env.OPENAGENT_SQLITE_URL ||
        "sqlite+aiosqlite:///./apps/api/data/openagent.db";
      console.log(`[api] database: embedded SQLite (${apiEnv.DATABASE_URL})`);
    } else {
      console.log(`[api] database: ${fileEnv.DATABASE_URL || process.env.DATABASE_URL || "(unset)"}`);
    }
    // Keep CORS narrow to the actual local web origin.
    if (!fileEnv.CORS_ORIGINS && !process.env.CORS_ORIGINS) {
      apiEnv.CORS_ORIGINS = `http://localhost:${WEB_PORT}`;
    }

    // Spawn the interpreter directly: cmd.exe quoting breaks on install
    // paths containing spaces (e.g. "D:\open agent").
    startService(
      "api",
      python,
      ["-m", "uvicorn", "openagent.main:app", "--reload", "--host", "0.0.0.0", "--port", String(API_PORT)],
      ROOT,
      apiEnv,
    );
  }

  if (ONLY.includes("worker")) {
    const python = apiPython();
    console.warn("[worker] The worker requires Redis (REDIS_URL). Start it via `pnpm dev:docker` or `--db=postgres`.");
    startService(
      "worker",
      python,
      ["-m", "worker.main"],
      path.join(ROOT, "apps", "worker"),
      { PYTHONPATH: path.join(ROOT, "apps", "worker", "src") },
    );
  }

  if (children.length === 0) {
    console.error("Nothing to start. Use --only=web,api,worker.");
    process.exit(1);
  }
}

main();
