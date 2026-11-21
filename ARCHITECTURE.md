# OpenAgent Architecture

## Current Repository State

OpenAgent is an open-source AI Workforce Operating System. The monorepo contains the Next.js web app, FastAPI API, background worker, shared TypeScript packages, and the Electron desktop shell.

## Chosen Stack

### Frontend
- **Framework**: Next.js 15+ (App Router, React 19)
- **Language**: TypeScript (strict mode)
- **Styling**: Tailwind CSS
- **UI Components**: shadcn/ui + Radix UI primitives
- **State Management**: React Context + TanStack Query
- **Build Tool**: Turborepo

### Backend
- **Framework**: FastAPI
- **Language**: Python 3.11+
- **Validation**: Pydantic v2
- **ORM**: SQLAlchemy 2.0 (async)
- **Migrations**: Alembic
- **Database**: PostgreSQL 15+
- **Cache/Queue**: Redis 7+
- **Testing**: pytest + httpx

### Infrastructure
- **Containerization**: Docker + Docker Compose
- **CI/CD**: GitHub Actions
- **Monorepo**: pnpm workspaces + Turborepo

## Architectural Decisions

### 1. Monorepo Structure

```
openagent/
├── apps/                 # Deployable applications
│   ├── web/             # Next.js frontend
│   ├── api/             # FastAPI backend
│   ├── worker/          # Background job worker
│   ├── desktop/         # Electron desktop shell
│   └── docs/            # Documentation site
├── packages/            # Shared internal packages
│   ├── core/            # Core utilities, constants
│   ├── config/          # Configuration management
│   ├── types/           # Shared TypeScript/Python types
│   ├── logger/          # Structured logging
│   ├── database/        # Database abstraction
│   ├── security/        # Security utilities
│   ├── agent-runtime/   # Agent execution runtime
│   ├── workflow-engine/ # Workflow execution engine
│   ├── model-router/    # LLM model routing
│   ├── tool-system/     # Tool abstraction
│   ├── mcp/             # Model Context Protocol
│   ├── memory/          # Memory system
│   ├── browser/         # Browser automation
│   ├── sandbox/         # Secure execution sandbox
│   ├── evaluator/       # Evaluation framework
│   └── sdk/             # Public SDK
├── integrations/        # Third-party integrations
├── marketplace/         # Marketplace packages
├── infrastructure/      # Infrastructure as code
├── examples/            # Example applications
├── tests/               # Cross-cutting tests
└── docs/                # Architecture documentation
```

### 2. Dependency Rules

```
apps/web       → packages/types, packages/config, packages/core
apps/api       → packages/types, packages/config, packages/core, packages/logger, packages/database, packages/security
apps/worker    → packages/types, packages/config, packages/core, packages/logger, packages/database
packages/*     → packages/core, packages/config, packages/types (where applicable)
packages/core  → (no internal dependencies)
packages/config → packages/core
packages/types → packages/core
packages/logger → packages/core, packages/config
packages/database → packages/core, packages/config, packages/logger
packages/security → packages/core, packages/config
```

**Forbidden Dependencies:**
- Frontend packages → Backend packages
- Domain packages (agent-runtime, workflow-engine) → UI packages
- Infrastructure packages → Domain packages
- Circular dependencies between any packages

### 3. Layered Backend Architecture

```
API Layer (FastAPI routes)
    ↓
Application Services (Business logic, orchestration)
    ↓
Domain Layer (Entities, value objects, domain services)
    ↓
Infrastructure Layer (Database, external APIs, Redis)
```

### 4. Multi-Tenant Foundation

All organization-scoped resources include `organization_id`. Tenant isolation is enforced at the database/repository layer, not just in application code.

### 5. API Versioning

All application APIs are versioned: `/api/v1/`, `/api/v2/`, etc. No unversioned endpoints.

### 6. Error Handling

Consistent error contract:
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "request_id": "uuid",
    "details": {}
  }
}
```

### 7. Request Correlation

Every request gets a `request_id` (UUID) propagated through logs, errors, and responses.

### 8. Configuration

Environment-based configuration with `.env.example` for documentation. No secrets in code.

### 9. Logging

Structured JSON logging in production, pretty logging in development. Request correlation IDs included.

## Implemented Capability Layers

1. **Agent Runtime** — pluggable agent execution engine
2. **Workflow Engine** — visual authoring, execution, and the `orchestration` node
3. **Model Router** — multi-provider LLM routing
4. **Tool System** — extensible tool framework with SSRF-guarded HTTP tools
5. **MCP** — Model Context Protocol integration
6. **Memory** — vector + relational memory
7. **Browser** — provider-neutral Playwright automation and research
8. **Code Agent** — repository intelligence, patches, review, and git workflows
9. **Sandbox** — isolated execution with production fail-closed policy
10. **Approvals & Guardrails** — risk engine, policy engine, human-in-the-loop resume
11. **Evaluator & Quality Control** — rubrics, deterministic verifiers, LLM judge, quality gates
12. **Universal Integrations** — connector registry, OAuth, webhooks, guarded HTTP/SQL
13. **Marketplace & Packages** — listings, publisher verification, distribution, install/rollback
14. **Commerce, Billing & Entitlements** — provider-neutral billing, quotas, creator revenue
15. **Developer Platform** — extensions, SDK, CLI, signing, and lifecycle management

Each layer is organization-scoped, audited, and covered by the API/web test suites. Detailed design notes live under `docs/`.

## MP28 Developer Platform (Extension Architecture)

Single canonical extension architecture (§3, §13): one extension type
registry (20 types), one permission catalog, one trust model. All extension
kinds are entries in `apps/api/src/openagent/developer/types.py` — never
parallel plugin systems. Guides: `docs/developers/`.

```
Developer
   ├─► SDK (@openagent/sdk TS · openagent-sdk Python)
   ├─► CLI (openagent: init/dev/validate/test/build/package/publish/deploy/rollback)
   └─► Portal (web: /templates /skills /marketplace /publisher)
                     │
                     ▼
   FastAPI: /organizations/{id}/developer/* + /organizations/{id}/extensions/*
   validate → test → package → scan → compat → sign → publish → install
                     │
        ┌────────────┼────────────┬──────────────┐
        ▼            ▼            ▼              ▼
   Tool Runtime   Sandbox   ConnectorEngine  Model Router
   (tool:execute) (sandbox: (OAuth/SSRF       (model:invoke,
                   execute)  guard)            LLM judge)
        └────────────┴────────────┴──────────────┘
                     ▼
   PostgreSQL (extension_* tables) · Redis queues (orchestration/marketplace/commerce)
   Audit log ← every lifecycle transition · Security events
```

### Backend modules (`apps/api/src/openagent/developer/*`)

| Module | Responsibility |
|--------|----------------|
| `types.py` | Extension-type registry (20), permission catalog, trust levels, lifecycle, `DEVELOPER_EVENTS` (20), compat matrix 1.x |
| `manifest.py` | `ExtensionManifest` schema for `openagent.yaml`; rejects unknown types/permissions, embedded secret values, unsafe network declarations |
| `permissions.py` | `check_permissions` enforcement + trust model (trust never bypasses authorization; no self-grant) |
| `security.py` | Static scans, secret detection (`SECRET_PATTERNS`), install-hook safety; `blocks_publish`/`blocks_install` |
| `signing.py` | Ed25519 signing/verification, `key_id` rotation, revocation at verify time |
| `packaging.py` | Deterministic `.oaext` archives, checksums, SBOM, provenance |
| `versioning.py` | Semver, constraints, `check_compatibility`, `DEPRECATIONS` |
| `service.py` | Lifecycle orchestration: validate→test→build→package→scan→compat→sign→deploy→health→activate; quarantine/rollback |
| `mocks.py` | Deterministic offline test mocks (LLM, Tool, Connector, MCP, Browser, Sandbox, Memory, Workflow, Agent, Webhook, Storage) |
| `config.py` | `DeveloperSettings` (`OPENAGENT_DEV_*`; secret override forbidden in production), deployment stages |
| `errors.py` | Public error taxonomy (16 codes), webhook HMAC-SHA256 sign/verify (`v1`, 300s replay window) |

### SDK / CLI packages

| Package | Role |
|---------|------|
| `packages/sdk` (`@openagent/sdk`) | Public TS SDK: agents/tools/workflows/connectors/MCP/memory/evaluations/extensions/events; Bearer auth, org scoping, idempotency keys |
| Python `openagent-sdk` | Same contract for Python runtimes (auth, pagination, idempotency, webhook verify) |
| `packages/cli` (`openagent`) | Full lifecycle CLI: init/login/logout/whoami/dev/validate/test/build/package/publish/deploy/rollback + resource groups; JSON/CI mode, exit codes 0–6 |
| `packages/connector-sdk` | Connector manifest validation + scaffolding (server re-validates authoritatively) |
| Extension-sdk builder API | `defineAgent/defineTool/defineWorkflowNode/defineConnector/defineMCPServer/defineSkill/defineEvaluator` used by all examples/templates |

### DB tables (`apps/api/src/openagent/db/models/developer.py`)

`developer_projects`, `developer_project_members`, `developer_environments`,
`extension_definitions`, `extension_versions`, `extension_installations`,
`extension_deployments`, `developer_webhooks`, `developer_webhook_deliveries`,
`extension_analytics_daily`, `extension_trust_records`. All rows
organization-scoped (global rows readable cross-tenant only via the catalog
path with visibility checks).

### Routes (`apps/api/src/openagent/api/v1/developer.py`)

- `/organizations/{id}/developer/projects[. ..]` — projects, members, environments
- `/organizations/{id}/developer/environments`, `/developer/webhooks`, `/developer/events`, `/developer/usage`
- `/organizations/{id}/extensions[. ..]` — CRUD, versions, validate/test/package/artifact/publish/sign/install/disable/quarantine/rollback
- `/api/v1/developer/sdk|/errors|/events` — public metadata (no auth)

## Database Design Principles

- UUID primary keys
- `created_at`, `updated_at` on all tables
- Soft deletes (`deleted_at`) where appropriate
- JSONB for flexible metadata
- Organization-scoped tables include `organization_id`
- Audit trails for sensitive operations
- Alembic for migrations

## Security Baseline

- Opaque SHA-256 session tokens in HttpOnly, SameSite=Lax cookies; Secure cookies in production
- Argon2 password hashing; Fernet+HKDF credential envelope encryption
- CSRF double-submit validation and trusted-proxy-aware rate limiting
- Organization-scoped RBAC enforced in application/repository layers
- CORS configured per environment
- Security headers (CSP, HSTS, etc.)
- Input validation at API boundaries
- SSRF-aware outbound HTTP (scheme/IP/DNS/redirect validation)
- No stack traces in production errors
- Secrets only in environment variables
- Dependency scanning in CI