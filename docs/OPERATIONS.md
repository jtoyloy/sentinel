# Sentinel operations

Synthetic data only. Sentinel is one API/worker process with PostgreSQL as the
source of truth and Redis Streams as a durable transport. Run one API replica;
WebSocket fan-out across replicas is not implemented. No regulator certification
or production capacity is implied.

## Start and provision

Set a non-default `POSTGRES_PASSWORD` in the root `.env` (URL-safe characters or
URL-encode it in a separately supplied DATABASE_URL), then `docker compose up --build -d`.
Existing database volumes keep their original PostgreSQL password: changing the
environment does not rotate a database role. Use a deliberate database operation
when rotating it. Local services bind to loopback. Use a TLS reverse proxy for
remote access, set explicit CORS_ORIGINS and SESSION_COOKIE_SECURE=true, and restrict
PostgreSQL/Redis access at the network boundary. Compose's Vite frontend is for
local synthetic evaluation; use a reviewed static-serving deployment for remote use.

Apply migrations before creating users. No seeded production identities exist.
From repository root, after installing backend dependencies:

```sh
cd backend
alembic upgrade head
cd ..
backend/venv/bin/python scripts/create_user.py investigator --role ADMIN
```

The provisioner prompts without echo and confirms the password. Automation may
supply SENTINEL_USER_PASSWORD through an ephemeral secret environment variable;
never place passwords in source control or shell command arguments. For Compose,
run `docker compose exec api python /scripts/create_user.py investigator --role ADMIN`.
Compose mounts the scripts read-only; the provisioner detects the container /app directory.

VIEWER reads; ANALYST ingests synthetic feeds and investigates; ADMIN additionally
manages policies, exemptions, security-master records and recovery. Verify the
server response to unauthorized mutations, not merely disabled UI controls.
Sessions last eight hours, are hashed in the database, and rotate on login.
Logout revokes the server session. Deactivate a user with an authorized operator
SQL transaction (`UPDATE users SET active=false WHERE username=...`) to revoke
access immediately. Clear auth_sessions for emergency global revocation.

## Observe and respond

`GET /health` returns 503 if PostgreSQL or Redis fails. Authenticated
`GET /api/operations` provides actual delivery backlog, stream/cursor state,
worker progress/failures and retained counts. Inspect `docker compose logs api`
for worker exceptions. Do not infer pipeline freshness from health alone.

If Redis is unavailable after startup, events and outbox intent remain in
PostgreSQL. Restore Redis and verify the pending outbox drains and cursor advances.
Use the ADMIN operations recovery endpoint with `REPUBLISH_PENDING` to retry
pending records. `REBUILD_STREAM` republishes retained events for transport loss;
allow it to finish and verify cursor/backlog status. It preserves source rows and
alert uniqueness; WebSocket delivery can repeat and REST remains authoritative.
No `docker compose down -v`, stream deletion, or raw-event deletion is part of
routine recovery. Stop ingestion during large recovery to obtain a stable audit.

If PostgreSQL fails, stop intake, restore its connectivity, confirm the migration
revision with `alembic current`, then restart the API. For a failed migration,
inspect its transaction and the actual schema before retrying. Never edit applied
0001/0002 or drop tables to silence migration errors. Take a database backup before
an upgrade. `pg_dump -Fc` and a separately tested `pg_restore` into an isolated
database are the recovery baseline; Redis AOF complements but cannot replace a
PostgreSQL backup. Verify roles and session revocation after restoring an older DB.

## Validation and measurements

```sh
backend/venv/bin/python -m pytest backend/tests -q
cd frontend
npm ci
npm run build
cd ..
backend/venv/bin/python scripts/benchmark.py --runs 3 --output benchmark.json
```

Set SENTINEL_USERNAME/SENTINEL_PASSWORD for the benchmark. It writes real synthetic
replays and waits for persisted replay events and run-specific alerts; it fails on HTTP errors or
a pipeline timeout. Results include per-run latency, p50/p95, actual before/after
counts, pipeline snapshot, client CPU and client RSS. RSS units are platform
native (Linux KiB, macOS bytes). Server CPU/RSS are explicitly unmeasured. The
sequential workload and 100ms polling cannot establish capacity or a latency SLO;
run on an otherwise idle stack and review retained data and workload composition.
CI applies migrations to actual PostgreSQL, runs the complete test suite and
frontend build, starts the real API/Redis worker and archives actual benchmark
measurements plus API logs. Tests using SQLite are persistence unit/integration
checks and do not substitute for the live PostgreSQL migration/worker exercise.

## Scope and retention

Raw events/evidence and audit logs are append-only. Sessions and login-throttling
records are mutable operational data. Expired login attempts are pruned during
login; expired sessions require an operator retention job at larger scale.
Configure backup, audit retention and account lifecycle to your organization's
requirements before any non-synthetic use. External identity providers, MFA,
password reset, distributed socket delivery, regulatory submission and calibrated
surveillance models are outside this implementation.


Use scripts/platform_smoke.py --help for full cross-domain validation with private
synthetic role fixtures. scripts/fault_smoke.py --help creates its own PostgreSQL
DB, Redis6387 and API8001, tests actual outage/restart/rebuild/quarantine, and cleans
up only owned resources. The primary API8000 and Redis6379 remain untouched.

Benchmark samples wait for outbox_pending=0 and cursor=stream_last_id as well as
exact run-specific alerts/events. Retained history and polling affect observed
rate; this is not certified capacity. Local samples are under docs/benchmarks/.
