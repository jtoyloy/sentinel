# Sentinel engineering coordination

Read CLAUDE.md, docs/ARCHITECTURE.md, docs/CONTRACTS.md and docs/PLATFORM_CONTRACTS.md.
Preserve migration 0001, existing trading data, source interfaces, and surveillance behavior.
Use synthetic data only. No invented certifications or benchmark numbers.

## Ownership for platform expansion

The integrator owns main.py, shared migration 0002, existing event/alert integration,
app/platform.py, platform investigations/operations, root docs, and integration tests.
Specialists own disjoint new modules and their focused tests:
- employee: app/domains/employees.py, tests/test_employees.py
- communications: app/domains/communications.py, tests/test_communications.py
- restrictions: app/domains/restrictions.py, tests/test_restrictions.py
- data: app/domains/data.py, tests/test_data.py
- frontend: frontend/**
- QA/security: app/core/security.py, app/api/auth.py, app/models/auth.py,
  tests/test_security.py, tests/conftest.py, scripts/**, .github/**,
  Dockerfiles, docker-compose.yml, docs/OPERATIONS.md

Each domain module contains its SQLAlchemy models, Pydantic input schemas, services,
and APIRouter. Import Base and UTCDateTime from app.models.models; use generic
Uuid and JSON().with_variant(JSONB, 'postgresql'). Domain model registration happens
in app/platform.py; routes registered by integrator. Do not edit another owner's
files or shared migration. Ask integrator to resolve contract changes. Do not commit
or push unless requested. All tables added through one integrator-owned frozen 0002.

Money uses Decimal/Numeric, times are aware UTC, identifiers bounded. Immutable
source records, idempotent feed keys and append-only audit. Persist failures and
explanations. API mutation handlers commit once; nested services flush only.
Pagination limit (1..1000) and offset>=0; collection GET responses are arrays.
Validate normal controls and false positives; use actual database persistence in
tests. No tests that only mirror implementation. All auth/RBAC enforced server-side.
