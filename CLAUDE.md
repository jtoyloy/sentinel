# Sentinel project instructions

## Purpose and source of truth

Deliver a working local surveillance slice with persistent real data and tested
behavior. Read docs/ARCHITECTURE.md and docs/CONTRACTS.md before changing shared
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
  same transaction. Identity is self-entered in this local slice, not authenticated.
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
