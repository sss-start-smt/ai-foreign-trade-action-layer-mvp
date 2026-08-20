# FlowOrder → Tencent CloudBase P0 Deployment Guide

## Decision

GO, with a revised architecture:

Browser → CloudBase HTTP Function (FastAPI + existing static frontend, same origin) → CloudBase PostgreSQL

Do **not** split the frontend into a separate static-hosting deployment for P0. Do **not** add Gitee just for runtime access. The current FastAPI app already mounts `/static` and serves `static/index.html` from `/`, so one HTTP function is the lowest-risk migration.

## Why the current code is compatible

- Current app is FastAPI and already serves its own HTML/CSS/JS.
- Current DB abstraction already supports `postgresql://` through SQLAlchemy.
- `requirements.txt` already contains SQLAlchemy/Alembic/psycopg2-binary.
- CloudBase officially supports FastAPI HTTP functions listening on port 9000.
- CloudBase PostgreSQL officially supports PostgreSQL-protocol direct connections from cloud functions/backend services.

## Changes made in this preflight build

1. `scf_bootstrap`: Python 3.11 + Uvicorn + port 9000.
2. `FLOWORDER_SERVERLESS_MODE=true`:
   - database initialization is synchronous at instance startup;
   - Agent job and intake job no longer depend on a daemon/background thread surviving after the HTTP response.
3. PostgreSQL serverless pool defaults reduced to 2 + overflow 1.
4. `DB_SCHEMA` added so FlowOrder can use `floworder` schema and later coexist with other portfolio projects.
5. PG startup table verification reduced to one `information_schema` query instead of ~46 round trips.
6. CloudBase PostgreSQL baseline migration generated from a fresh DB upgraded through the current Alembic head `p7q8r9s0t1u2`.
   - 52 business tables are present.
   - all 46 runtime `PG_REQUIRED_TABLES` are covered.

## Cost / stop-loss strategy

### P0 — spend ¥0 first

Create ONE free CloudBase PostgreSQL environment in Shanghai and validate only:

- `/health` returns 200
- `/ready` returns `database_ready=true`
- `/` loads the FlowOrder login UI
- `limin / demo123` login works
- `/api/orders` returns the D19 demo dataset
- `/api/dashboard` returns actionable tasks
- basic PostgreSQL insert/read/update works

The free CloudBase environment has a fixed 3-second cloud-function timeout and 256 MB memory. Therefore **do not use P0 failure on Agent/Coze execution as a platform rejection**; Agent is tested only after upgrade.

### P1 — only after P0 passes

Upgrade the same environment to Personal (currently ¥19.9/month), then configure:

- function memory: start at 512 MB
- function timeout: 120 seconds
- `FLOWORDER_SERVERLESS_MODE=true`
- `DB_SCHEMA=floworder`
- `DB_POOL_SIZE=2`
- `DB_MAX_OVERFLOW=1`
- `SEED_D19_DEMO_DATA=true`
- `DATABASE_URL=<CloudBase PostgreSQL direct connection URL>`
- existing Coze / model / ERP read-only environment variables as needed

Then test Agent, intake, Human Review, Waiting and D15 states.

## Hard stop rule

Do not spend days debugging the platform.

If P0 cannot pass the five basic gates after one focused deployment/debug cycle, stop the CloudBase route and use Lighthouse instead. Do not modify FlowOrder product semantics to fit a hosting platform.

## Database initialization

Migration file:

`cloudbase/migrations/20260820000100_floworder_baseline.sql`

It was generated from a brand-new SQLite DB upgraded through every current Alembic migration to head `p7q8r9s0t1u2`, then reflected and compiled to PostgreSQL DDL. It creates a dedicated `floworder` schema and all current business tables/indexes.

Apply the migration through CloudBase PostgreSQL migration/SQL management, **not** through the runtime direct database account. CloudBase restricts direct-login DDL for normal runtime roles.

## What has been verified locally

- Python syntax for the changed runtime files: PASS.
- `scf_bootstrap` shell syntax: PASS.
- CloudBase adapter contract tests: 4/4 PASS.
- Baseline contains 52 tables and covers 46/46 runtime-required tables.
- Serverless-mode local smoke using a fresh SQLite DB:
  - startup `/ready`: PASS
  - demo seed: 17 orders
  - login: PASS
  - `/api/orders`: 17 demo orders
  - `/api/dashboard`: data returned
  - reviews: 4 demo reviews
- Serverless job-contract simulation:
  - Agent POST finishes a simulated worker within the request and persists COMPLETED: PASS
  - Intake POST finishes a simulated worker within the request and persists COMPLETED: PASS

## What is NOT verified yet

These require a real CloudBase environment and are the P0/P1 gates:

- Python 3.11 dependency installation on CloudBase (especially compiled wheels such as pydantic-core/psycopg2-binary).
- Real CloudBase PostgreSQL baseline application.
- Direct PostgreSQL connection credentials/network path from the HTTP function.
- Real Coze/model outbound call latency under the Personal timeout.
- Mainland mobile/PC access to the CloudBase generated HTTP domain.

Do not claim these as passed until the real cloud smoke is complete.
