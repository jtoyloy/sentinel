# Sentinel

A working local trade surveillance slice: Python/FastAPI, React/TypeScript,
PostgreSQL, Redis Streams, WebSockets, and Docker Compose. It flags suspected
wash trades and spoofing with explainable evidence and investigator dispositions.

## Run with Docker Compose

```sh
docker compose up --build -d
```

Open [the dashboard](http://localhost:5173) or [OpenAPI](http://localhost:8000/docs).
The API container applies the initial Alembic migration automatically. PostgreSQL
and Redis AOF data use named volumes. `docker compose down` preserves them.

```sh
docker compose exec api pytest tests -q
docker compose exec frontend npm run build
docker compose logs -f api
```

## Run locally

Requires Python 3.9+ (Docker uses 3.11), Node 22+, PostgreSQL 15+, and Redis 7+.
Create a PostgreSQL role `sentinel` with password `sentinel_dev` and database
`sentinel`, and start Redis at `127.0.0.1:6379`.

```sh
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/alembic upgrade head
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Set `DATABASE_URL`, `REDIS_URL`, and `CORS_ORIGINS` in backend/.env or environment
to override defaults. Vite proxies REST and WebSockets to port 8000; no browser
hostnames are hardcoded.

## Use

1. Run a seeded wash-trade, spoofing, mixed, or normal replay from the dashboard.
2. Watch live alerts, filter the queue, and open an alert.
3. Inspect its original persisted order/execution/cancellation records and metadata.
4. Enter investigator identity and notes to change status; inspect the durable audit trail.

Replay accepts `seed` and timezone-aware `base_time`. The same inputs reproduce
market content and rule results, while each run has isolated identity namespaces.
Run metadata is persisted. Replay completes after atomic persistence; alerts arrive
as the Redis worker processes it. Normal replay produces no alerts.

External event ingestion supports an optional UUID `event_id` for idempotent
retries. Reusing an ID with changed content returns 409. Batches are atomic.
Trading events and investigation audit records reject SQL updates and deletes.

## Verify

```sh
backend/venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
```

The automated suite uses SQLite for isolated persistence tests and replaces Redis
transport where needed. It exercises real validation, detection, evidence, replay,
audits, outbox retries, cursor recovery, and deduplication. To exercise the running
PostgreSQL/Redis/API stack, including actual WebSockets (adds one mixed replay):

```sh
backend/venv/bin/python backend/tests/live_smoke.py
```

Architecture, contracts, delivery semantics, and scope are documented in
[CLAUDE.md](CLAUDE.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and
[docs/CONTRACTS.md](docs/CONTRACTS.md). The API publishes executable contracts at
`/openapi.json`. `schema.sql` is the frozen initial PostgreSQL schema; use Alembic
for application setup. The migration refuses incompatible pre-existing tables
without changing their data; legacy schemas require an explicit data migration.

This is a local investigative vertical slice. Scores rank heuristic evidence, not
probabilities or findings of misconduct. Alerts are snapshots of available evidence;
late events can change the assessment. Authentication, user authorization,
production retention policy, broker feed integration, and throughput certification
are outside this slice. Run one API process; the embedded worker publishes live
messages to connections in that process.
