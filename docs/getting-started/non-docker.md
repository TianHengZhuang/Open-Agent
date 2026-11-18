# Non-Docker

Run everything from source without Docker. There are two profiles:

- **Zero-dependency local (default):** `pnpm dev` runs the Next.js web app and
the FastAPI backend on an embedded SQLite database. No PostgreSQL, no Redis,
no Docker. This is the fastest way to reach the browser dashboard; the API
bootstraps its schema from the models at startup. Some advanced features that
depend on Redis queues or PostgreSQL-only indexing are limited in this mode.
- **Full local stack:** PostgreSQL 15+ and Redis 7+ (below). Use
`pnpm dev --db=postgres` for the complete feature set.

## Zero-dependency local mode

```bash
git clone https://github.com/mdsabibahamedsad/Open-Agent.git
cd Open-Agent
pnpm install
pnpm dev            # web: http://localhost:3000  api: http://localhost:8000
```

## Full PostgreSQL + Redis profile

```bash
pnpm setup          # deps + .env
pnpm doctor         # confirm Postgres/Redis reachable

# Backend virtualenv (once)
python -m venv apps/api/.venv
apps/api/.venv/Scripts/python -m pip install -e "apps/api[dev]"   # Windows
# source apps/api/.venv/bin/activate && pip install -e "apps/api[dev]"  # macOS/Linux

pnpm db:setup       # migrate + seed dev data
pnpm dev --db=postgres   # web + api against PostgreSQL/Redis, Ctrl+C stops all
```

Individual services: `pnpm dev:web`, `pnpm dev:api`, `pnpm dev:worker` (worker
needs Redis).

## If Postgres/Redis are missing

`pnpm doctor` tells you exactly that, with options:

1. Install locally (PostgreSQL 15+, Redis 7+)
2. Infrastructure-only Docker: `pnpm infra:up` (keeps code native — see [hybrid](hybrid.md))
3. Full Docker: `docker compose up --build`

## Environment

Point `.env` at your local services (defaults already do):

```text
DATABASE_URL=postgresql+asyncpg://openagent:openagent@localhost:5432/openagent
REDIS_URL=redis://localhost:6379/0
```
