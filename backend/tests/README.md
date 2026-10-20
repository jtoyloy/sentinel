# Verification

From the repository root:

```sh
backend/venv/bin/python -m pytest backend/tests -q
```

- test_detection.py: persisted detector boundaries, false positive controls,
  execution ordering, owner identity, cancellation identity and fills, deterministic generation.
- test_api_pipeline.py: real API and SQLite persistence, isolated seeded replays,
  validation, atomic idempotency, evidence, investigator audit, statistics,
  Redis transport failures, cursor restart, duplicate and delayed deliveries.
- test_integration.py: original ingestion/detection/alert service integration tests.

SQLite tests use portable UUID/JSON types; PostgreSQL uses native UUID/JSONB.
Only Redis transport is replaced in pipeline tests. PostgreSQL-only triggers and
migrations require a real server and were verified separately.

With the application, PostgreSQL and Redis running, test actual stream delivery,
WebSocket alerts, exact persisted evidence and investigator history (adds data):

```sh
backend/venv/bin/python backend/tests/live_smoke.py
```


V2 adds employees, communication evidence/both correlation arrival orders,
policy/exemption snapshots, broker normalization/reconciliation/reporting,
authentication, investigations and recovery. The full suite has 104 tests.
Frozen PostgreSQL migrations/triggers are separately checked on a disposable DB.
Live HTTP/WebSocket and isolated fault scripts are in scripts/. See
../../docs/VALIDATION.md for actual observations and limitations.
