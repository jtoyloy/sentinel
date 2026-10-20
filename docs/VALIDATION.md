# Validation — October 20, 2026

All exercised data and identities are synthetic. These observations are not
certifications, regulator acceptance or production capacity guarantees.

## Automated local checks

- **104 backend tests passed** (11.39 seconds final full run). Real SQLite
  persistence covers domains, APIs, sessions/RBAC, immutable evidence, malformed
  records/savepoint rollback, enrichment, policy snapshots, investigations, late
  correlation, outbox retries, quarantine and replay deduplication. Controlled
  failure tests substitute only Redis transport.
- **3 Playwright E2E tests passed** against actual PostgreSQL/Redis/FastAPI
  (7.4 seconds): real cookie login; persisted HR, messages, policies, independent
  exemption approval, case escalation, brokers/reconciliation, audited CSV download,
  recovery and replay; viewer permissions/logout; 390px mobile navigation. No mocked routes.
- TypeScript/Vite production build passed; npm audit reported zero vulnerabilities.

## Real PostgreSQL and Redis

- Disposable PostgreSQL upgraded 0001 -> 0002 with a source event inserted before
  upgrade: event preserved, zero ORM drift, 14 immutability triggers, mutation rejected.
- **Nine live cross-domain grouped checks passed in 2.901 seconds.** Three roles,
  HR/quality/enrichment including forged claims, both communication/trade arrival
  orders, BLOCK/REVIEW/ALLOW and independent exemptions, linked case escalation and
  closure, authenticated audits, broker discrepancy classes, CSV SHA256 audit,
  live authenticated WebSocket/logout revocation, pending recovery and deduplication.
  See [complete evidence](platform-e2e-2026-10-20.json).
- **Isolated fault smoke passed in 8.111 seconds** using separate Redis6387/API8001
  and a disposable DB. Outage: health503, eight retained events/four pending records.
  Same-AOF restart: eight events/two alerts, backlog drained. Full stream rebuild:
  eight events/two alerts, cursor caught up. Invalid reference: retried three times
  then quarantined; later replay completed at twelve events/three alerts. Own
  database/processes cleaned up, primary services untouched.
  See [recovery evidence](recovery-e2e-2026-10-20.json).

## Measured local workload

[Complete samples](benchmarks/2026-10-20-local.json): three sequential mixed replays,
112 events each, local macOS/Apple Silicon with PostgreSQL/Redis. Each sample waits
for run-specific alerts **and full outbox/stream cursor drain**.

| Observation | Measured value |
| --- | ---: |
| Events / new alerts | 336 / 6 |
| Total wall time | 6.770 seconds |
| Observed workload rate | 49.632 events/second |
| Persistence p50 / p95 | 200.160 / 239.867 ms |
| Alert observed p95 | 1,687.198 ms |
| Pipeline drained p95 | 2,226.019 ms |

Retained events: 1,813 before, 2,149 after. History affects detection cost. Three
samples and 100ms polling cannot establish percentile distributions, peak throughput,
an SLO or production capacity. Server CPU/RSS are unmeasured; client resources are
in the JSON. This final run supersedes exploratory measurements that observed first
alerts without waiting for all stream messages to finish.

## Reproduce

```sh
backend/venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build && npm run test:e2e
```

Browser tests need provisioned role fixtures (frontend/README.md). Live checks use
scripts/platform_smoke.py and scripts/benchmark.py with private credentials.
scripts/fault_smoke.py creates isolated disposable local resources; see --help.
Scripts add synthetic data. Secrets, AOF files and logs stay in ignored .runtime/
or temporary locations. Docker is absent locally; GitHub CI independently runs
PostgreSQL migrations, backend/live/browser checks and both Docker image builds.
Refer to its actual run for hosted results; YAML validation alone is not CI success.

## Boundaries

External HR/inbox/broker/Snowflake accounts and regulatory submissions are not
connected. CSV is an audited synthetic export, not a certified filing. SSO/MFA,
password reset, tenant isolation, production retention/legal holds, distributed
socket fan-out and HA remain deployment work. Profiles represent the latest observed
effective feed version; scheduled future activation and bitemporal queries are not
implemented. Surveillance remains heuristic and provisional; supplemental evidence
does not automatically retract prior findings.
