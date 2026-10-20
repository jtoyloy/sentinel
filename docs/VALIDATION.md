# Verified 2026-10-20

- Backend: 53 pytest tests passed (SQLite persistence; real services/detectors;
  Redis transport substituted for controlled failure/recovery tests).
- Frontend: TypeScript and Vite production build passed; npm audit reported no
  vulnerabilities for the installed lockfile.
- Actual local PostgreSQL 16 + Redis + FastAPI + WebSocket smoke: mixed replay
  atomically stored 112 events, generated exactly one wash and one spoofing alert,
  sent both through WebSocket, returned exact linked evidence with replay metadata,
  persisted investigator status/history, and answered heartbeat.
- API restart retained event/alert counts and persisted stream cursor; no new alerts
  were generated without new events. Outbox drained to zero unpublished entries.
- Browser: live counts/queue, seeded replay, evidence drawer including cancellation,
  investigator disposition, and audit history verified through the running UI.
- PostgreSQL migration: fresh upgrade/current head, zero ORM schema drift,
  refusal of legacy table without changing its row, immutable-event/audit mutation
  rejection, and direct schema.sql initialization verified in a disposable database.
- Compose proxy targets were resolved and checked for both REST and WebSocket.
  Full Docker Compose execution was not possible: Docker is absent on this host.

Development replay and browser-verification records remain in the local database.
This validation establishes the local vertical slice; it does not establish
production security, certified compliance, or sustained throughput.
