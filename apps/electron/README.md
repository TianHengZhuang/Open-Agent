# OpenAgent Desktop (Electron)

Windows desktop wrapper around the Next.js web UI (`apps/web`).

## Run (dev)

1. Start backend + web:
```powershell
docker compose up -d --build
```
2. Start desktop shell:
```powershell
pnpm --filter @openagent/electron install
pnpm --filter @openagent/electron start
# optional custom URL:
# $env:OPENAGENT_WEB_URL="http://localhost:3000"; pnpm --filter @openagent/electron start
```

App auto-retries `http://localhost:3000` every 3s until web is up.

## Build .exe (Windows)

```powershell
pnpm --filter @openagent/electron dist:win
```

Output in `apps/electron/dist/`:
- `OpenAgent Setup *.exe` (NSIS installer)
- `OpenAgent-Portable-*.exe` (no install)

Note: this packages only the Electron shell. It still needs
`docker compose up -d` running locally (or `OPENAGENT_WEB_URL` pointing
to a hosted web). Full offline bundling (Node + Docker images inside exe)
is future work.
