# Sentinel workspace

React/TypeScript frontend for the authenticated Sentinel API. All displayed records,
counts, policy outcomes and health measurements come from the persistent backend.
The trade view retains the live alert stream, evidence drawer and deterministic replay.
Other queues refresh after mutations; the overview polls every 10 seconds and operations
every 5 seconds. CSV downloads call the audited export endpoint.

Run the migrated API, PostgreSQL and Redis as documented in the root README, then:

```sh
npm ci
npm run dev
```

`API_PROXY_TARGET` configures the Vite REST/WebSocket upstream (default
`http://127.0.0.1:8000`). Auth uses a same-origin HttpOnly session cookie; credentials
and session tokens are never stored in localStorage. Provision accounts with the
backend account tool. VIEWER can inspect records; ANALYST can operate feeds and cases;
ADMIN can publish policies, maintain securities, approve another user's exemptions
and recover delivery. The backend independently enforces every permission.

## Validation

```sh
npm run build
npx playwright install chromium
SENTINEL_TEST_USERS=/absolute/path/synthetic-users.json npm run test:e2e
```

The credential JSON must contain `ADMIN`, `ANALYST`, and `VIEWER` objects, each with
provisioned `username` and `password` strings. Keep this file outside version control.
Tests also accept `SENTINEL_WEB_URL` and default to the ignored local
`../.runtime/platform-users.json` file. No accounts are created or passwords printed.
The live tests persist synthetic HR records, messages, policies, decisions, cases,
broker executions and report audits. Use a dedicated synthetic test database for CI.
Missing credentials skip these live browser tests explicitly.

The suite exercises the real cookie login, domain forms, evidence inspection,
independent exemption approval, case escalation, normalization, reconciliation,
audited CSV download, recovery, replay, viewer controls, logout persistence and
mobile navigation. It uses no mocked API routes or fabricated measurements.
