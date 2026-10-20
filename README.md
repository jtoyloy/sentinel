# Sentinel

Sentinel is a working institutional compliance platform for synthetic data, built
with FastAPI, React/TypeScript, PostgreSQL, Redis Streams, WebSockets, and Alembic.
It combines employee and communication feeds, trade surveillance, firmwide
restrictions, investigations, reconciliation, reporting, and operational monitoring.

## Implemented systems

- **Employees:** deterministic HR feeds, current profiles, effective-dated change
  history, identity/account mappings, persistent data-quality issues, trade enrichment.
- **Communications:** email/chat normalization, preserved SHA256 evidence, versioned
  phrase/entity rules, employee-linked trade correlation in either arrival order.
- **Trading:** atomic immutable order/execution ingestion, idempotent event IDs,
  Redis outbox delivery, explainable wash/spoofing alerts, deterministic replay.
- **Restrictions:** versioned scoped securities policies, ALLOW/BLOCK/REVIEW checks,
  immutable decision snapshots, independently approved expiring exemptions.
- **Investigations:** linked trade alerts and domain signals, evidence references,
  authenticated notes, escalation, closure/reopen, append-only timelines.
- **Data/reporting:** two different broker adapters, preserved raw records, security
  master, missing/duplicate/mismatched execution reconciliation, CSV export audits.
- **Reliability/security:** role-based sessions, protected APIs and WebSockets,
  durable cursor/heartbeats, poison-message quarantine, audited recovery, CI.

All feeds are synthetic. No external employee, inbox, broker or regulator account is connected.

## Run with Docker Compose

```sh
cp .env.example .env
# Choose a local PostgreSQL password in .env, then:
docker compose up --build -d
docker compose exec api python /scripts/create_user.py investigator --role ADMIN
```

Open [the platform](http://localhost:5173) and sign in with the provisioned user.
The provisioner prompts securely. There are no default application credentials.
[OpenAPI](http://localhost:8000/docs) documents all request and response contracts.
API routes require a session; VIEWER reads, ANALYST investigates/ingests, ADMIN
additionally controls policies, approvals, security master, and recovery.

The API applies migrations automatically. PostgreSQL and Redis AOF use named
volumes. `docker compose down` preserves them. Existing PostgreSQL volumes retain
their original role password; changing .env does not rotate that password.

## Run locally

Requires Python 3.9+ (Docker uses 3.11), Node 22+, PostgreSQL 15+, Redis 7+.
Create PostgreSQL role/database and configure backend/.env with DATABASE_URL,
REDIS_URL and CORS_ORIGINS. Defaults support the original loopback development
PostgreSQL role `sentinel`, database `sentinel`, password `sentinel_dev`.

```sh
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/alembic upgrade head
cd ..
backend/venv/bin/python scripts/create_user.py investigator --role ADMIN
cd backend
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Vite proxies API and WebSocket requests. Compose sets API_PROXY_TARGET to the
API container; local development uses 127.0.0.1:8000. Run one API process.

## Exercise the platform

1. Generate an HR feed in Employees and inspect mappings/history/quality issues.
2. Ingest or simulate communications and inspect rules, hashes, and correlated events.
3. Use Trade Surveillance for live wash/spoofing scenarios and exact evidence.
4. Create a restriction policy, check a pre-trade request, request an exemption,
   and approve it from a different ADMIN identity.
5. Link trade alerts/domain signals into a case, add notes, escalate and close it.
6. Simulate ALPHA and BETA broker feeds, reconcile discrepancies, download audited CSV.
7. Inspect actual pipeline health, backlog, cursor, quarantines, and ADMIN recovery.

Restrictions are advisory pre-trade checks. Historical event ingestion is retained
for surveillance rather than discarded when a policy would have blocked a trade.
Replay seed/base_time reproduce market content with isolated identities per run.

## Validate

```sh
backend/venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
```

For live cross-domain HTTP/WebSocket checks, provision synthetic ADMIN/ANALYST/VIEWER
accounts and use `scripts/platform_smoke.py --help`. For browser tests use the
frontend test instructions. Live scripts add synthetic data to the selected database.

```sh
# Provisioned credentials supplied through secret environment variables:
backend/venv/bin/python scripts/benchmark.py --runs 3 --output .runtime/benchmark.json
```

See [validation evidence](docs/VALIDATION.md), [operations runbook](docs/OPERATIONS.md),
[architecture](docs/ARCHITECTURE.md), [platform contracts](docs/PLATFORM_CONTRACTS.md),
and [project instructions](AGENTS.md). CI applies actual PostgreSQL migrations,
starts the API/Redis worker, runs backend/build checks, and records measurements.
The original schema.sql is the frozen 0001 snapshot; migrate to 0002 with Alembic.

This is a functional synthetic evaluation platform, not a certified regulatory
system. Scores rank evidence rather than calibrated probabilities or proof of
misconduct. Production SSO/MFA, multi-tenant isolation, regulator-specific filing,
external feed integration, Snowflake credentials, HA/socket fan-out, retention
policy, and certified capacity remain outside this implementation.
