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

## Isolated QA Docker environment

Milestone 5 uses a separate Compose project in `docker-compose.qa.yml`. It has its own `nexus_qa_test` MySQL database and project-scoped volume, private `qa_app` and `qa_data` networks, and no host-published ports. It does not join the development Compose networks or mount the development MySQL volume. QA containers have no Docker socket, privileged mode, or host networking. The QA networks are Docker `internal` networks; this is useful isolation for disposable local testing, not a security boundary for running deliberately malicious code.

The API, migration/bootstrap commands, and test entrypoints run `infra/qa/qa-guard.sh` in a fixed mode passed by the Compose entrypoint, not a guard-mode environment variable. The guard requires the exact QA database name, user and service host, validates secret formatting without printing values, and requires the API/Alembic connection URLs and direct API-test DB settings to match. API tests also require `MYSQL_TEST_DATABASE_KIND=disposable` and `BASE_URL=http://api:8000`. UI tests receive no database credentials or database network access; their guard requires `NEXUS_QA_MODE=true`, the QA target markers, and the fixed `http://proxy` base URL. In QA mode Playwright disables traces, screenshots, video, and the HTML reporter before test execution and suppresses its automatic page DOM snapshot; invalid mode values fail configuration rather than falling back to local artifact capture. Playwright can still create `error-context.md` with matcher-generated assertion details on failure, so do not put secrets in assertion messages or publish that file. Local runs without QA mode retain failure traces and screenshots. The test entrypoints run fixed commands so Compose command/environment overrides cannot silently redirect them. An operator who deliberately overrides a container entrypoint can bypass container guards; do not do so.

Create a private QA environment file and replace every placeholder with independently generated values. The database, root, JWT, administrator, and RBAC-test secrets must each be at least 64 hexadecimal characters and mutually distinct; the guard rejects placeholder, low-diversity, or non-hex values. Keep the actual file private and untracked:

```bash
cp .env.qa.example .env.qa
chmod 600 .env.qa
# Edit .env.qa using a password manager or secure random-value generator.
```

Use the explicit project, file, and environment file in every command below. The configuration check validates required target markers and secret strength without starting MySQL. `config --quiet` validates Compose interpolation without printing rendered secrets.

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml config --quiet
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile tools run --rm qa-config-check
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml build api web api-tests ui-tests qa-unit-tests qa-migrate qa-bootstrap qa-reset-admin qa-migrate-admin-email
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml up -d qa-mysql mailhog
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile tools run --rm qa-migrate
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile tools run --rm -it qa-bootstrap
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml up -d api web proxy
```

The bootstrap command reuses `app.commands.bootstrap_admin`; the `qa-bootstrap` service always enters through the fixed `qa-guard.sh bootstrap` entrypoint, which validates the exact QA database target before calling the shared command. It idempotently seeds roles and permissions and prompts for the administrator password without echoing it. Enter the same password configured as `QA_ADMIN_PASSWORD` so the HTTP and UI test fixtures can log in. API and UI tests receive only the credentials they need: API/UI tests use the administrator account; `QA_RBAC_PASSWORD` is a separate password assigned to secondary users created by API tests. The bootstrap service does not receive either password through its environment. Direct operator invocation of `app.commands.bootstrap_admin` outside the guarded QA service is outside this QA isolation guarantee. Migrations and bootstrap run only when explicitly requested.

If the existing QA administrator password needs correction, use the separately guarded maintenance command after the QA database is available. It validates the exact QA database target, reads `QA_ADMIN_EMAIL`, and prompts twice for the replacement password without placing it in arguments or environment variables:

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile tools run --rm -it qa-reset-admin
```

The command updates only that existing superuser, clears the temporary-password change/expiry fields, and revokes its active sessions in one transaction. It does not create accounts or seed roles, and it does not run migrations or bootstrap.

If the QA administrator address is still `qa-admin@example.test`, update only `QA_ADMIN_EMAIL` in the ignored `.env.qa` to `qa-admin@example.com` before migrating the existing account. The address uses the reserved `example.com` domain and is accepted by the application's normal `EmailStr` validation; production email validation is unchanged. Bootstrap and both API/UI test services consume this same configured value. After confirming the old value in the disposable QA account, run this guarded one-time migration. It requires the exact old and new values, checks that the old account is a superuser, rejects a conflicting target, and is safe to repeat after success. It changes only the email column and preserves account ID, password hash, roles, and permissions.

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile tools run --rm qa-migrate-admin-email --old-email qa-admin@example.test --new-email qa-admin@example.com
```

The guard requires the new argument to match `QA_ADMIN_EMAIL`, `APP_ENV=test`, and the exact `qa-mysql:3306/nexus_qa_test` URL with the restricted `nexus_qa_runner` account. Do not change the command's project, Compose file, database target, or arguments. This command is not run automatically.

Run test suites on demand after initialization:

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile test run --rm api-tests
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile test run --rm ui-tests
```

Backend unit tests use a separate image built from `apps/api/requirements-dev.txt`; pytest is installed at image-build time, never at container runtime. The `qa-unit-tests` service has no network (`network_mode: none`), receives only an in-memory SQLite URL and a test signing key, and its guard rejects MySQL URLs or host settings. Its fixed command runs the guarded maintenance-command tests and the authentication SQL lock-order regression with pytest and no cache provider. Build and run them explicitly:

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml build qa-unit-tests
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml --profile test run --rm qa-unit-tests
```

Stop the QA services without deleting test data:

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml down --remove-orphans
```

To remove only the disposable QA project's volume after confirming you no longer need its data, run this destructive command:

```bash
docker compose --project-name nexus-qa --env-file .env.qa -f docker-compose.qa.yml down --volumes --remove-orphans
```

Do not remove the `--project-name nexus-qa` or `-f docker-compose.qa.yml` arguments from cleanup commands. The QA project contains its own explicitly named volume and does not refer to `mysql_data`.

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

### Milestone 5 suite registry and execution policy

The backend owns a version-controlled, immutable QA suite registry in `apps/api/app/services/qa_suite_registry.py`. It defines `unit-backend`, `api-auth`, `api-regression`, and `ui-regression`, each with a fixed QA runner service, repository-relative test selectors, timeout ceiling, description, and enabled flag. The selectors correspond to the guarded unit, API, and UI test services in `docker-compose.qa.yml`; they are trusted metadata for a future runner and are not executed by this milestone.

The pure execution policy accepts registered suite IDs and a bounded timeout request. It rejects unknown or disabled suites, arbitrary command/selector/Docker/environment fields, invalid QA environment descriptors, development database/service targets, excessive timeouts, and requests at the configured concurrency limit. Decisions contain safe reason codes and trusted selectors, never a shell command or caller-supplied value. The QA environment descriptor contains no credentials and must be assembled by trusted application configuration. Planning text and provider output remain untrusted suggestions; they cannot define registry entries, selectors, services, or environment values. Current Milestone 4 plans cite deterministic candidate-suite evidence paths, so a future integration must explicitly map validated plan and impact evidence to these stable registry IDs rather than interpreting case text or treating path references as commands.

This is a policy decision only: it does not reserve concurrency or launch containers. Phase 3 must add an atomic concurrency reservation, a fixed service/selector-to-launcher mapping, cancellation and timeout cleanup, and end-to-end isolation checks before any suite can execute. The execution entrypoint must preserve the existing QA guard and must not accept agent-controlled Docker arguments, command strings, filesystem paths, or environment overrides.

The first QA foundation exposes authenticated run management at `POST /api/v1/qa/runs`, `GET /api/v1/qa/runs`, `GET /api/v1/qa/runs/{run_id}`, and `POST /api/v1/qa/runs/{run_id}/cancel`. Starting and cancelling runs requires `qa:execute`; listing and viewing runs requires `qa:read`. These permissions are checked by the API. Apply the Alembic migration with `docker compose exec api alembic upgrade head` before using the endpoints.

Runs use the built-in mock provider and need no AI key, GitHub access, test runner, or extra service. A completed foundation run is explicitly marked `foundation_mock`, reports `not_assessed`, and includes a `not_run` test execution summary. It does not analyze a repository, claim test execution, or report simulated findings. The provider and ordered workflow stage interfaces are in the API service layer; test execution results use a generic schema that is not coupled to Allure. There is no QA dashboard or OpenAI integration in this step.

Runs are persisted, but this step uses FastAPI in-process background tasks rather than a durable queue. In-flight work is not resumed after an API restart; stale active records are marked `timed_out` when read or cancelled after their configured progress timeout.

### GitHub pull request ingestion

The webhook receiver is `POST /api/v1/integrations/github/webhook`. It accepts signed `pull_request` events for `opened`, `reopened`, and `synchronize`; other events/actions are acknowledged as ignored. Configure `GITHUB_WEBHOOK_SECRET`, a read-only `GITHUB_TOKEN` with Pull requests read access, and an exact comma-separated `GITHUB_ALLOWED_REPOSITORIES` allowlist such as `owner/repository`. These values are passed to the API container by Compose and are not returned by the API. Keep real values in the local `.env`, not in Git. GitHub should send the `X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery` headers.

The webhook HMAC is checked before payload parsing. Repository identity is checked against the allowlist before the fixed GitHub API host is contacted. PR metadata and changed-file pages are read with the configured token; a complete file count and stable base/head snapshot are required before a run is created. API calls have bounded response sizes, retries, per-request timeouts, and an overall ingestion deadline. Each QA run retains a typed GitHub context with the delivery, repository, PR, branch, and SHA information. A per-PR sequence and immutable revision occurrence allow force-push reversions such as A→B→A without conflating runs. The current GitHub head decides whether an event is stale; an older webhook base SHA alone does not make a matching-head event stale. When the base changes for the same head, the client rechecks and retrieves a fresh changed-file snapshot, recorded as a new occurrence. Stale head deliveries are recorded as ignored and do not move the latest revision pointer. Duplicate deliveries can re-dispatch a still-pending run, while the orchestrator's atomic claim prevents duplicate execution. Because execution uses in-process background tasks, recovery depends on a webhook redelivery; this step has no durable queue or startup sweep. Superseded runs retain their original SHA/context. This step still performs no repository cloning, code analysis, or test execution.

Apply the GitHub ingestion migration with `docker compose exec api alembic upgrade head`. Configure the same webhook secret in the GitHub webhook settings and in `GITHUB_WEBHOOK_SECRET`; never commit either secret.

Apply database changes with `docker compose exec api alembic upgrade head`. Run backend contract tests with `docker compose --profile test run --rm api-tests` and UI tests with `docker compose --profile test run --rm ui-tests`.
