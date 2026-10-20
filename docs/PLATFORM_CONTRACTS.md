# Platform expansion v2 — shared integration contracts

Existing v1 event/alert contracts and 0001 migration remain valid. One FastAPI app,
one PostgreSQL database, existing Redis pipeline; no additional microservices.
Each domain module exports `router` (full /api prefix) and SQLAlchemy models.
Register imports in app/platform.py and Alembic env, then create frozen additive 0002.
Use UUID primary keys (except externally meaningful employee/security IDs), generic
JSON with PostgreSQL JSONB variant, UTCDateTime, and aware Pydantic timestamps.
All synthetic simulators deterministic for seed; return persisted counts/IDs, not fake data.

## Access and common evidence

app.core.security exports `get_current_user`, `require_reader`, `require_analyst`,
`require_admin`; Principal has id, username, role (ADMIN/ANALYST/VIEWER).
Each domain router uses reader globally; mutation endpoints add analyst or admin as
appropriate. Services do not import security. Actor derived from authenticated user,
never trusted from submitted text. Session cookie HttpOnly + SameSite=Lax, random
opaque server-side token hash, password PBKDF2 with per-user salts, expiry, login rate
limit, cookie mutation Origin validation. No default production credentials.
POST /api/auth/login {username,password} -> {username,role}; POST /api/auth/logout;
GET /api/auth/me -> {username,role}; provisioning via backend scripts/create_user.py.
Integrator handles existing API/WebSocket auth and authenticated actor attribution.
QA supplies test override fixture for legacy tests without weakening production.

`app.platform` exports `ComplianceSignal` ORM and async `record_signal(db, *, domain,
rule, fingerprint, severity, title, explanation, evidence)`; flush only, caller commits.
Signal: id UUID, domain COMMUNICATION/RESTRICTION/DATA, rule, fingerprint unique,
severity LOW/MEDIUM/HIGH/CRITICAL, status OPEN/INVESTIGATING/RESOLVED/FALSE_POSITIVE,
title, explanation, evidence JSON, created_at. Evidence contains typed references
{communications:[uuid], events:[uuid], employees:[id], decisions:[uuid], reconciliations:[uuid]}
and may add rule metrics. Do not put raw private data in logs (all data synthetic).
GET /api/signals returns Signal[]; cross-domain investigation APIs below.

## Employee — app/domains/employees.py

Employee model exported: employee_id str PK, full_name, email unique normalized,
department, role, status ACTIVE/INACTIVE, manager_id optional, updated_at.
EmployeeAccount model: id UUID, employee_id FK, firm_id, account_id, unique(firm_id,account_id).
EmployeeChange immutable: id, employee_id, effective_at, payload, source_key unique.
FeedIssue: id, source, source_key, reason, payload, created_at.
GET /api/employees?search=&department=&limit=&offset= -> Employee[] incl account_count;
GET /api/employees/{employee_id} -> Employee + accounts[] + history[].
POST /api/employees/feed {records:[{source_key,employee_id,full_name,email,department,role,
status,effective_at,manager_id?,accounts:[{firm_id,account_id}]}]} -> {accepted,rejected,duplicates,issues:[]}.
Invalid per-record content goes to persistent issues; envelope validates bounded record count.
POST /api/employees/simulate?seed=42&count=12 -> same feed result, persisted seeded HR records.
GET /api/employees/quality -> FeedIssue[]. GET /api/employees/resolve?identity= ->
{employee_id,match_type} or 404; ambiguous identities reject 409.
Export async `resolve_employee(db, *, email=None, account_id=None, firm_id=None, trader_id=None)`
-> Employee|None. `enrich_event(db, event_data)` -> dict employee_id/department/role or {};
root ingestion merges enrichment into stored metadata without changing source payload.
Changes ordered by effective_at; stale feeds kept in history without reverting profile.

## Communications — app/domains/communications.py

Communication: id UUID, source_key unique, channel EMAIL/CHAT, timestamp, sender,
recipients JSON list, subject, body, employee_id optional, content_sha256, raw_payload JSON.
GET /api/communications?channel=&employee_id=&limit=&offset= -> Communication[].
POST /api/communications/ingest {messages:[{source_key,channel,timestamp,sender,recipients,
subject?,body}]} -> {accepted,duplicates,signals_generated}.
POST /api/communications/simulate?seed=42 -> same result. Rules for guaranteed return,
inside information, concealment; explicit keyword/entity spans and rule versions in evidence.
Match employee email/account using employee module helper. Correlate ticker mentions with
persisted executions linked to employee within +/-24h; include exact event IDs in signals.
GET /api/communications/{id} -> record + signals[] + related_events[].
GET /api/communications/{id}/evidence -> preserved record with hash verification.
Only suspicious content flags; ordinary trading discussion negative control. No external LLM required.

## Restrictions — app/domains/restrictions.py

Policy: id UUID, name, version int, status ACTIVE/RETIRED, instrument_id uppercase,
action BLOCK/REVIEW, reason, effective_from, effective_to?, department?, created_by,
unique(name,version); creating new version retires previous active version atomically.
GET /api/restrictions/policies -> Policy[]. POST same {name,instrument_id,action,reason,
effective_from,effective_to?,department?} -> Policy; ADMIN mutation.
POST /api/restrictions/check {employee_id,instrument_id,side:BUY/SELL,quantity,price} ->
Decision {id,employee_id,instrument_id,decision:ALLOW/BLOCK/REVIEW,reasons:[],policy_ids:[],
policy_versions:[],created_at,exemption_id?}; persist each check with exact policy snapshot.
GET /api/restrictions/decisions -> Decision[].
POST /api/restrictions/exemptions {employee_id,instrument_id,reason,expires_at} -> Exemption
{id,status:PENDING,...}; GET same -> Exemption[].
POST /api/restrictions/exemptions/{id}/approve {notes} -> approved exemption; ADMIN,
no self approval, unexpired exemptions only, preserve decision/approval audit.
GET /api/restrictions/audit -> audit rows. Expired/retired policy logic and scoped
employees enforced. Signals for BLOCK and REVIEW decisions. These are advisory
pre-trade checks; event ingestion records historical activity and is never silently blocked.

## Data — app/domains/data.py

Security: instrument_id str PK, name, isin unique, exchange, currency, asset_class.
GET /api/data/securities -> Security[]. POST /api/data/securities {instrument_id,name,isin,
exchange,currency,asset_class} -> Security (ADMIN).
POST /api/data/feeds/{ALPHA|BETA}/simulate?seed=42 ->
{batch_id,received,normalized,rejected,duplicates}; two genuinely different broker payload
formats normalized via exported normalization function to TradingEventCreate.
POST /api/data/feeds/{ALPHA|BETA}/ingest {records:[broker-specific dictionaries]} -> same result.
BrokerRecord: raw payload, source id, source event key unique, normalized event UUID,
quality/error status, immutable hash. Repeated payloads idempotent, changed key conflicts
recorded as quality issues. Feed ingestion persists canonical TradingEvent+outbox through
IngestionService with commit=False. Enrichment attaches security master fields and source.
GET /api/data/batches -> persisted summaries. GET /api/data/quality -> quality issues.
POST /api/data/reconcile -> persisted run with counts/status/issues; compare ALPHA and BETA
execution references, detecting MISSING/DUPLICATE/MISMATCH independently of arrival order.
GET /api/data/reconciliations -> reconciliation runs; GET /api/data/reconciliations/{id} -> run + issues[].
GET /api/reports/trades.csv?from_time=&to_time= -> real canonical execution CSV with stable
columns, UTC times, Decimal prices; safe CSV cells; persist export audit (format/version,
row count,SHA256,requester). GET /api/reports/exports -> Export[]; synthetic regulatory-style
export, no claim of regulator-specific certification. Optional Snowflake omitted unless real credentials exist.

## Integrator — investigations and operations

GET /api/overview -> {employees,communications,trade_events,trade_alerts,signals,open_cases}.
GET/POST /api/investigations; POST {title,priority:LOW/MEDIUM/HIGH/CRITICAL,
owner?,summary,signal_ids:[],alert_ids:[]} -> Case with id,status OPEN/ESCALATED/CLOSED,
created_at,updated_at. GET /api/investigations/{id} -> Case + timeline[] + linked evidence.
POST /api/investigations/{id}/actions {action:NOTE/ESCALATE/CLOSE/REOPEN,notes} -> case.
GET /api/signals; PATCH /api/signals/{id} {status,notes} -> signal + append audit.
GET /api/operations -> real DB/Redis health, outbox_pending, stream_length,cursor,
worker last success/failure/counters, retained event count, feed issue count.
POST /api/operations/recover {mode:REPUBLISH_PENDING|REBUILD_STREAM} -> actual operation
counts; ADMIN only, no deletion of source events, append operation audit.

## Frontend

Keep current trade queue/drawer/replay as Trade Surveillance view. Add authenticated
navigation for overview, employees, communications, restrictions, investigations,
data/reconciliation/reports, operations. Real forms and empty/error/loading states;
role-aware controls, CSV download actually calls report. Login cookie flow, no tokens
in localStorage. Poll domain lists after mutations; REST recovery on socket reconnect.

## Validation ownership

Each domain adds focused persistence/API tests. QA adds security tests, CI using
PostgreSQL/Redis, runbooks and measured benchmark script; integrator runs migrations,
full suite, real stack, browser and cross-domain E2E. Existing tests may override
get_current_user via test-only fixtures. Production must always enforce authentication.
