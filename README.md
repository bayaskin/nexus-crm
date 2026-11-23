# Nexus CRM

A containerized CRM, Order Management, and Inventory application built as a realistic target for a reusable QA automation framework.

## Initial architecture

- `apps/web`: React + TypeScript + Vite frontend
- `apps/api`: FastAPI backend, SQLAlchemy, MySQL driver, Alembic-ready structure
- `automation/ui`: Playwright + TypeScript browser tests
- `automation/api`: pytest + HTTPX API tests
- `infra/nginx`: local reverse proxy
- `mysql`: MySQL 8.4 container with a persistent Docker volume

The backend is a modular monolith. Business modules will be separated by domain boundaries inside `apps/api/app/modules`.

## Prerequisites

- Docker Engine and Docker Compose v2
- Git

## Start locally

```bash
cp .env.example .env
# Generate a key, then replace JWT_SECRET_KEY in .env with its output.
openssl rand -hex 32
# Also set local development database passwords in .env.
docker compose up --build -d
```

Open:

- App: http://localhost:8080
- API docs: http://localhost:8080/api/docs
- API health: http://localhost:8080/api/health
- API readiness (includes MySQL): http://localhost:8080/api/health/ready
- MailHog inbox: http://localhost:8025

MySQL is published on host port `3307` for local database tools. Containers use the internal hostname `mysql:3306`.

## Run tests

```bash
docker compose --profile test run --rm api-tests
docker compose --profile test run --rm ui-tests
```

## Useful commands

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f mysql
docker compose down
# Destructive: removes the database volume and all local database data.
docker compose down -v
```

## Initial implementation sequence

1. Confirm container networking, health checks, and persistent MySQL storage.
2. Add SQLAlchemy models and Alembic migrations.
3. Implement authentication, users, roles, and server-side RBAC.
4. Implement customers and contacts.
5. Implement products, stock movements, and inventory rules.
6. Implement orders, order items, state transitions, and audit events.
7. Build React routes, forms, tables, filters, and accessible dynamic components.
8. Expand API, UI, integration, and end-to-end tests; add fixtures and isolated test data.

## Security note

The credentials in `.env.example` are local-development placeholders only. Never use them in a shared or production environment. `.env` is excluded from Git.

`JWT_SECRET_KEY` has no application default. The example value is deliberately rejected; generate a private key with `openssl rand -hex 32` and put it in `.env` before starting Compose. Settings reject missing, short, low-diversity, and known placeholder keys in every environment. Use a separately generated secret for each deployment and provide it through the deployment environment or secret manager. Run the focused configuration checks with `cd apps/api && pip install -r requirements-dev.txt && PYTHONPATH=. pytest -q tests/test_config.py`.

## User management and access control

After applying migrations, bootstrap the initial administrator with `docker compose exec api python -m app.commands.bootstrap_admin --email admin@example.com --name "Nexus Admin"`. The command prompts for a password and idempotently seeds permissions and roles. Existing role names (Administrator, Sales Manager, Sales Representative, Support, and Read-only) remain supported alongside Admin, Manager, QA, and User.

Open **User management** from the sidebar to search and filter accounts, create or edit users, view effective access, activate/deactivate accounts, and reset passwords. New accounts receive a cryptographically generated temporary password, require a password change at first login, and receive credentials by email by default. Temporary passwords expire after `TEMPORARY_PASSWORD_HOURS` (24 hours by default). Temporary passwords are never included in API responses by default. `EXPOSE_TEMPORARY_PASSWORDS=true` is available only with `APP_ENV=development`; production and test startup reject it. When delivery fails, the API reports the failure without returning the password; after fixing SMTP/MailHog, an administrator can use Reset password to generate and email a fresh temporary password. Credential values are not logged.

Access tokens are checked against their database session on every protected request. Password changes, password resets, and deactivation revoke active sessions, so existing access tokens stop working immediately rather than remaining valid until JWT expiry. Refresh also checks account activity and temporary password expiry; reactivation does not restore revoked sessions.

Effective access is role grants plus explicit user allows, with explicit user denies applied last. The API resolves permissions from current database relationships for every request, so access changes apply immediately. All user and permission management is protected by API permissions. Endpoints include `GET/POST /v1/users`, `GET/PATCH /v1/users/{id}`, `POST /v1/users/{id}/reset-password`, `GET /v1/users/roles`, `GET /v1/users/permissions`, and `POST /v1/auth/password/change`. User listing accepts `search`, `role`, `status`, `page`, and `page_size` parameters.

The API reads `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, and `APP_LOGIN_URL` from environment configuration. Local Compose starts MailHog at SMTP `mailhog:1025`; view captured messages at http://localhost:8025. Override these variables when using another SMTP service.

## Agentic QA foundation

The first QA foundation exposes authenticated run management at `POST /api/v1/qa/runs`, `GET /api/v1/qa/runs`, `GET /api/v1/qa/runs/{run_id}`, and `POST /api/v1/qa/runs/{run_id}/cancel`. Starting and cancelling runs requires `qa:execute`; listing and viewing runs requires `qa:read`. These permissions are checked by the API. Apply the Alembic migration with `docker compose exec api alembic upgrade head` before using the endpoints.

Runs use the built-in mock provider and need no AI key, GitHub access, test runner, or extra service. A completed foundation run is explicitly marked `foundation_mock`, reports `not_assessed`, and includes a `not_run` test execution summary. It does not analyze a repository, claim test execution, or report simulated findings. The provider and ordered workflow stage interfaces are in the API service layer; test execution results use a generic schema that is not coupled to Allure. There is no QA dashboard or OpenAI integration in this step.

Runs are persisted, but this step uses FastAPI in-process background tasks rather than a durable queue. In-flight work is not resumed after an API restart; stale active records are marked `timed_out` when read or cancelled after their configured progress timeout.

### GitHub pull request ingestion

The webhook receiver is `POST /api/v1/integrations/github/webhook`. It accepts signed `pull_request` events for `opened`, `reopened`, and `synchronize`; other events/actions are acknowledged as ignored. Configure `GITHUB_WEBHOOK_SECRET`, a read-only `GITHUB_TOKEN` with Pull requests read access, and an exact comma-separated `GITHUB_ALLOWED_REPOSITORIES` allowlist such as `owner/repository`. These values are passed to the API container by Compose and are not returned by the API. Keep real values in the local `.env`, not in Git. GitHub should send the `X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery` headers.

The webhook HMAC is checked before payload parsing. Repository identity is checked against the allowlist before the fixed GitHub API host is contacted. PR metadata and changed-file pages are read with the configured token; a complete file count and stable base/head snapshot are required before a run is created. API calls have bounded response sizes, retries, per-request timeouts, and an overall ingestion deadline. Each QA run retains a typed GitHub context with the delivery, repository, PR, branch, and SHA information. A per-PR sequence and immutable revision occurrence allow force-push reversions such as A→B→A without conflating runs. The current GitHub head decides whether an event is stale; an older webhook base SHA alone does not make a matching-head event stale. When the base changes for the same head, the client rechecks and retrieves a fresh changed-file snapshot, recorded as a new occurrence. Stale head deliveries are recorded as ignored and do not move the latest revision pointer. Duplicate deliveries can re-dispatch a still-pending run, while the orchestrator's atomic claim prevents duplicate execution. Because execution uses in-process background tasks, recovery depends on a webhook redelivery; this step has no durable queue or startup sweep. Superseded runs retain their original SHA/context. This step still performs no repository cloning, code analysis, or test execution.

Apply the GitHub ingestion migration with `docker compose exec api alembic upgrade head`. Configure the same webhook secret in the GitHub webhook settings and in `GITHUB_WEBHOOK_SECRET`; never commit either secret.

Apply database changes with `docker compose exec api alembic upgrade head`. Run backend contract tests with `docker compose --profile test run --rm api-tests` and UI tests with `docker compose --profile test run --rm ui-tests`.
