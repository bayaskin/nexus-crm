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
# Edit .env and set local development passwords.
docker compose up --build -d
```

Open:

- App: http://localhost:8080
- API docs: http://localhost:8080/api/docs
- API health: http://localhost:8080/api/health
- API readiness (includes MySQL): http://localhost:8080/api/health/ready

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
