"""Restriction policies and dual-control approval tested with real persistence."""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.models import Base
from app.core.database import get_db
from app.core.security import Principal, get_current_user
from app.domains.employees import Employee
from app.domains.restrictions import (
    Policy, Decision, Exemption, RestrictionAudit, PolicyInput, CheckInput, ExemptionInput,
    create_policy, check_permission, request_exemption, approve_exemption, router,
)

NOW = datetime.now(timezone.utc)
REQUESTER = Principal(uuid.uuid4(), 'requester', 'ADMIN')
APPROVER = Principal(uuid.uuid4(), 'approver', 'ADMIN')


def policy(**changes):
    return PolicyInput(**dict(name='Insider restriction', instrument_id='AAPL', action='BLOCK', reason='Synthetic deal watch',
                              effective_from=NOW - timedelta(days=1), **changes))


def check(**changes):
    fields = dict(employee_id='EMP-1', instrument_id='AAPL', side='BUY', quantity=100, price='100.12345678')
    fields.update(changes)
    return CheckInput(**fields)


@pytest_asyncio.fixture
async def db():
    import app.platform  # noqa: F401
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(Employee(employee_id='EMP-1', full_name='Synthetic One', email='one@example.test', department='Research', role='Analyst', status='ACTIVE', updated_at=NOW))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_versioned_decisions_preserve_policy_snapshot_and_signals(db):
    first = await create_policy(db, policy(), 'admin')
    blocked = await check_permission(db, check(), 'analyst', NOW)
    assert blocked.decision == 'BLOCK' and blocked.policy_versions == [1]
    second_input = policy()
    second_input.action, second_input.reason = 'REVIEW', 'Changed review scope'
    second = await create_policy(db, second_input, 'admin')
    reviewed = await check_permission(db, check(), 'analyst', NOW)
    await db.commit()
    assert first.status == 'RETIRED' and second.version == 2
    assert reviewed.decision == 'REVIEW' and reviewed.policy_ids == [str(second.id)]
    assert blocked.policy_snapshot[0]['action'] == 'BLOCK'
    assert blocked.policy_snapshot[0]['status'] == 'ACTIVE'
    assert blocked.price == check().price
    from app.platform import ComplianceSignal
    assert await db.scalar(select(func.count()).select_from(ComplianceSignal)) == 2
    assert await db.scalar(select(func.count()).select_from(RestrictionAudit)) == 5


@pytest.mark.asyncio
async def test_scope_expiry_future_policy_and_absence_allow_controls(db):
    await create_policy(db, policy(department='Trading'), 'admin')
    assert (await check_permission(db, check(), 'analyst', NOW)).decision == 'ALLOW'
    p = policy()
    p.effective_to = NOW
    await create_policy(db, p, 'admin')
    assert (await check_permission(db, check(), 'analyst', NOW)).decision == 'ALLOW'
    p = policy()
    p.effective_from = NOW + timedelta(days=1)
    await create_policy(db, p, 'admin')
    assert (await check_permission(db, check(), 'analyst', NOW)).decision == 'ALLOW'
    assert (await check_permission(db, check(instrument_id='MSFT'), 'analyst', NOW)).decision == 'ALLOW'
    with pytest.raises(HTTPException) as missing:
        await check_permission(db, check(employee_id='ABSENT'), 'analyst', NOW)
    assert missing.value.status_code == 404
    employee = await db.get(Employee, 'EMP-1')
    employee.status = 'INACTIVE'
    assert (await check_permission(db, check(), 'analyst', NOW)).decision == 'BLOCK'


@pytest.mark.asyncio
async def test_exemption_dual_control_expiration_and_inactive_employee(db):
    await create_policy(db, policy(), 'admin')
    exemption = await request_exemption(db, ExemptionInput(employee_id='EMP-1', instrument_id='AAPL', reason='Synthetic approved liquidation', expires_at=NOW + timedelta(days=1)), REQUESTER)
    assert (await check_permission(db, check(), 'analyst')).decision == 'BLOCK'
    with pytest.raises(HTTPException) as own:
        await approve_exemption(db, exemption.id, 'self approve', REQUESTER)
    assert own.value.status_code == 403
    await approve_exemption(db, exemption.id, 'Independent verification completed', APPROVER)
    allowed = await check_permission(db, check(), 'analyst')
    assert allowed.decision == 'ALLOW' and allowed.exemption_id == exemption.id
    with pytest.raises(HTTPException) as repeated:
        await approve_exemption(db, exemption.id, 'Again', APPROVER)
    assert repeated.value.status_code == 409
    assert (await check_permission(db, check(), 'analyst', NOW + timedelta(days=2))).decision == 'BLOCK'
    (await db.get(Employee, 'EMP-1')).status = 'INACTIVE'
    assert (await check_permission(db, check(), 'analyst')).decision == 'BLOCK'
    await db.commit()
    approved = await db.scalar(select(RestrictionAudit).where(RestrictionAudit.action == 'APPROVED'))
    assert approved.actor == 'approver' and approved.details['requester'] == 'requester'


@pytest.mark.asyncio
async def test_expired_pending_exemption_cannot_be_approved(db):
    exemption = await request_exemption(db, ExemptionInput(employee_id='EMP-1', instrument_id='AAPL', reason='Synthetic request', expires_at=NOW + timedelta(days=1)), REQUESTER)
    exemption.expires_at = NOW - timedelta(seconds=1)
    with pytest.raises(HTTPException) as error:
        await approve_exemption(db, exemption.id, 'Late approval', APPROVER)
    assert error.value.status_code == 409
    assert exemption.status == 'PENDING'
    with pytest.raises(HTTPException) as expired:
        await request_exemption(db, ExemptionInput(employee_id='EMP-1', instrument_id='AAPL', reason='Expired', expires_at=NOW - timedelta(days=1)), REQUESTER)
    assert expired.value.status_code == 422


@pytest.mark.asyncio
async def test_api_role_enforcement_and_real_admin_analyst_workflow(db):
    app = FastAPI()
    app.include_router(router)
    async def database():
        yield db
    role = {'value': 'VIEWER'}
    async def identity():
        return Principal(REQUESTER.id, 'requester', role['value'])
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = identity
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/restrictions/policies')).status_code == 200
        payload = policy().model_dump(mode='json')
        assert (await client.post('/api/restrictions/policies', json=payload)).status_code == 403
        assert (await client.post('/api/restrictions/check', json=check().model_dump(mode='json'))).status_code == 403
        role['value'] = 'ADMIN'
        assert (await client.post('/api/restrictions/policies', json=payload)).status_code == 200
        role['value'] = 'ANALYST'
        result = await client.post('/api/restrictions/check', json=check().model_dump(mode='json'))
        assert result.status_code == 200 and result.json()['decision'] == 'BLOCK'
        assert len((await client.get('/api/restrictions/decisions')).json()) == 1
        assert (await client.get('/api/restrictions/audit?limit=0')).status_code == 422
        assert (await client.get('/api/restrictions/policies?offset=-1')).status_code == 422


@pytest.mark.parametrize('updates', [{'price': 'NaN'}, {'price': '0'}, {'price': '1.123456789'}, {'quantity': 0}, {'quantity': 2147483648}, {'instrument_id': 'bad ticker'}])
def test_invalid_check_inputs(updates):
    with pytest.raises(ValidationError):
        check(**updates)


def test_policy_requires_aware_ordered_window():
    with pytest.raises(ValidationError):
        policy(effective_to=NOW - timedelta(days=2))
    values = policy().model_dump()
    values['effective_from'] = '2026-10-20T10:00:00'
    with pytest.raises(ValidationError):
        PolicyInput(**values)
