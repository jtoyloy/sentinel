# Sentinel project instructions

## Purpose and source of truth

Deliver a working local surveillance slice with persistent real data and tested
behavior. Read AGENTS.md, docs/ARCHITECTURE.md, docs/CONTRACTS.md and docs/PLATFORM_CONTRACTS.md before changing shared
interfaces. FastAPI OpenAPI defines executable request/response shapes; SQLAlchemy
models define the current schema; Alembic contains frozen versioned migrations.
README.md has supported run/test commands. Do not claim production readiness or
1000 events/sec without measurements.

## Architecture

- React + TypeScript + Vite uses same-origin REST and WebSocket proxy routes.
- FastAPI validates input and stores immutable events + outbox rows atomically.
- One embedded worker publishes committed outbox rows to Redis Streams, then
  detects using event-time windows for the received instrument.
- PostgreSQL stores deduplicated alerts, exact evidence IDs, investigator audit
  records, replay runs, outbox publication state, and stream processing cursor.
- Cursor and alerts commit together. WebSocket broadcasts follow commit. On
  reconnect, REST restores authoritative state.
- PostgreSQL advisory lock permits only one worker. Use one API process because
  WebSocket connection state is local; multi-process fan-out is outside this slice.

## Code ownership and contracts

Establish contracts before parallel work. Use independent subagents when helpful,
with nonoverlapping file ownership. Never overwrite another agent's changes.

Backend: app/api routes, models/schemas.py Pydantic contracts, models/models.py
SQLAlchemy persistence, services/{ingestion,pipeline,detection,alerting,generator}.py
business behavior, core database/config/Redis infrastructure.
Frontend: App.tsx queue/replay controls, components/AlertDetail.tsx investigation,
hooks/useApi.ts REST, hooks/useWebSocket.ts connection lifecycle, styles.css layout.

## Implementation rules

- No fake metrics, placeholder handlers, decorative controls, or silent loss.
- Store money as Decimal/Numeric. Require positive finite prices, positive bounded
  quantities, bounded identifiers, timezone-aware input timestamps.
- Client event UUIDs are idempotency keys; changed payloads conflict. Preserve
  atomic batch ingestion and transactional outbox records.
- Redis is at least once: publish failures remain pending; repeat stream messages
  must not create duplicate alerts. Commit cursor and new alerts atomically.
- Wash alerts require common beneficial ownership or same firm/account fallback,
  never common firm alone. Check either temporal execution order.
- Spoof alerts require >=10,000 units, cancellation within 15s, no observed fills,
  opposite executions while the order rests, and matching firm/account identity.
- Deduplicate wash by execution pair and spoof by order/cancel plus rule version.
  Explain the evidence, thresholds, ownership basis, and provisional assessment.
- Alert evidence is a first-observation snapshot. Late data does not automatically
  retract alerts; investigators must check history and record their disposition.
- Never update/delete raw trading events or audit history. PostgreSQL triggers
  enforce this; privileged database operations remain an operator responsibility.
- Investigator transitions require actor and notes, and append history in the
  same transaction. Identity is derived from authenticated sessions, never submitted actor text.
- Replay must use a local seeded RNG and aware base time; namespace run identities
  to prevent cross-run matches. Persist run parameters and events together.
- Add migrations for schema changes; never edit an applied migration. The initial
  migration refuses existing legacy tables. Do not drop user data to fix schema drift.
- Display request errors/loading/empty states. Cancel socket reconnect timers on
  unmount, bound state, and refetch REST after reconnect.

## Validation

Run backend/venv/bin/python -m pytest backend/tests -q and frontend npm run build.
Detector tests must include false positives and exact rule boundaries. API/pipeline
tests use real persistence and detector logic with only Redis transport replaced.
Test scenarios against actual PostgreSQL + Redis via tests/live_smoke.py when
changing delivery or schema behavior. Inspect the dashboard in a real browser
when changing layout or investigation flow. Document what was actually verified.


## Platform v2

The original trade surveillance is retained within the wider authenticated platform.
Domain ownership and exact interfaces are in AGENTS.md and docs/PLATFORM_CONTRACTS.md.
All domain modules register through app.platform.register_domains; importing models
must not start services. Migration 0002 is frozen additive DDL and must not be edited
after application. New schema changes require revision 0003 onward.

Profiles/account mappings are current operational state; original HR changes and
raw communication/broker payloads remain immutable evidence. Trusted employee and
security enrichment overwrites source assertions in canonical metadata; source_metadata
retains original input for provenance/idempotency. Supplemental communication/trade
signals handle late arrivals without rewriting first-observation evidence.

Restrictions require authenticated actors and independent exemption approval.
Never trust client roles, employee claims, policy versions, or investigator names.
Sessions use hashed server-side tokens and PBKDF2 passwords; no default users exist.
All platform APIs require role dependencies; health/auth entry points are exceptions.
WebSocket checks sessions at connection, periodically, and before each broadcast.

Run one embedded worker. Monitor persisted heartbeats and quarantined invalid stream
references; source/database errors must not be silently skipped. Recovery serializes
requests, pauses the worker, requeues retained outbox rows and restarts after commit.
Never delete source data to recover transport. Use docs/OPERATIONS.md for provisioning,
recovery, credential handling and measured benchmark execution.
