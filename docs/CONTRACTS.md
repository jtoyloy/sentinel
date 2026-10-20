# Sentinel vertical slice contracts

PostgreSQL is the source of truth. Immutable events and an outbox row are committed together; a worker publishes the outbox to Redis Streams and consumes it with a persisted cursor. Detection uses event time, any instrument, and a 90 second context on either side to support out-of-order arrivals. Alerts are deduplicated by rule version + sorted execution pair IDs for wash trades and order/cancel IDs for spoofing. Evidence is a snapshot at first detection, explicitly provisional for late events. Redis delivery is at least once. REST is authoritative on reconnect.

Existing event input and alert response shapes remain compatible with app/models/schemas.py. Event input additionally accepts optional event_id (UUID) for idempotent delivery. Event response includes metadata. All timestamps must include a timezone. Identifiers must be nonempty and bounded; price must be finite and positive.

GET /api/alerts?status=&alert_type=&instrument_id=&limit=&offset= returns Alert[]. GET /api/alerts/{id} returns Alert. GET /api/alerts/{id}/evidence returns TradingEvent[] ordered by timestamp, including original metadata. PATCH /api/alerts/{id} accepts {status, notes} (legacy actor is accepted but ignored; session identity is authoritative); returns Alert and persists an audit entry. GET /api/alerts/{id}/history returns audit entries. GET /api/stats returns {events, alerts, open_alerts, critical_alerts}. GET /api/events supports existing filters plus offset. GET /health checks PostgreSQL and Redis and returns 503 on dependency failure.

POST /api/replay/{wash_trade|spoofing|mixed|normal}?seed=42&base_time=2026-10-20T14:30:00Z returns {scenario, seed, base_time, run_id, events_generated, message}. Each run has unique order/account/trader/beneficial-owner namespaces to isolate runs. Generated content before run namespacing is deterministic for seed and base_time. Replay persists run metadata and events. Unknown scenarios return 422. Generator constructor supports base_time and seed; generate_scenario returns dictionaries accepted by TradingEventCreate. Replay completion means events have been stored; alerts arrive asynchronously through the real pipeline.

WS /ws/alerts emits {type:'ALERT', ...Alert} for new persisted alerts; client heartbeat receives {type:'PONG'}. Reconnect refetches REST. The Vite proxy serves /api, /health, /ws to port 8000; browser uses same-origin URLs.

DetectionEngine retains detect_wash_trades, detect_spoofing and process_detection_window signatures; process_detection_window additionally accepts optional end_time. Detection dictionaries retain type/detection/window_start/window_end. Spoofing detection includes cancel_event for evidence. Wash evidence requires common beneficial owner (metadata.beneficial_owner), or same firm and account when no explicit owner is available; common firm alone is insufficient. Same symbol, opposite executions in either temporal order, within 30s, price tolerance 1%, quantity tolerance 10%. Spoofing: large order >=10,000, cancellation <=15s, no execution, opposite side executions during resting interval belonging to same firm/account; evidence includes order, cancellation, and executions. Heuristics flag suspected activity, not proven intent or calibrated probabilities.

Schema changes use versioned, frozen Alembic migrations. The checked-in schema.sql matches the initial migration. Initialize an empty database with alembic upgrade head; schema changes to existing deployments require an explicit migration. No unverified production readiness or throughput claims.


Platform v2 extends these contracts in [PLATFORM_CONTRACTS.md](PLATFORM_CONTRACTS.md).
All /api business routes and WebSocket require authenticated sessions. The event
response adds nullable source_metadata, preserving original assertions before
trusted enrichment. GET /api/events also supports replay_run_id for exact run queries.
Migration 0002 preserves 0001 data while adding domain/auth/operational tables.
