"""Versioned advisory trading policies, dual-control exemptions and immutable decisions."""
import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, AwareDatetime, ConfigDict, model_validator
from sqlalchemy import Column, String, Text, Integer, Numeric, Uuid, ForeignKey, UniqueConstraint, JSON, CheckConstraint, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import require_reader, require_analyst, require_admin
from app.models.models import Base, UTCDateTime
from app.domains.employees import Employee


def now():
    return datetime.now(timezone.utc)


def J():
    return JSON().with_variant(JSONB, 'postgresql')


class Policy(Base):
    __tablename__ = 'restriction_policies'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default='ACTIVE')
    instrument_id = Column(String(50), nullable=False, index=True)
    action = Column(String(10), nullable=False)
    reason = Column(Text, nullable=False)
    effective_from = Column(UTCDateTime(), nullable=False)
    effective_to = Column(UTCDateTime(), nullable=True)
    department = Column(String(100), nullable=True)
    created_by = Column(String(100), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)
    __table_args__ = (UniqueConstraint('name', 'version', name='uq_restriction_policy_version'),
                      CheckConstraint("action IN ('BLOCK', 'REVIEW')", name='restriction_policy_action'),)


class Decision(Base):
    __tablename__ = 'restriction_decisions'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    employee_id = Column(String(50), ForeignKey('employees.employee_id'), nullable=False, index=True)
    instrument_id = Column(String(50), nullable=False)
    side = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    decision = Column(String(10), nullable=False)
    reasons = Column(J(), nullable=False)
    policy_ids = Column(J(), nullable=False)
    policy_versions = Column(J(), nullable=False)
    policy_snapshot = Column(J(), nullable=False)
    employee_snapshot = Column(J(), nullable=False)
    exemption_id = Column(Uuid, ForeignKey('restriction_exemptions.id'), nullable=True)
    requester = Column(String(100), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)
    __table_args__ = (CheckConstraint("decision IN ('ALLOW', 'BLOCK', 'REVIEW')", name='restriction_decision_outcome'),)


class Exemption(Base):
    __tablename__ = 'restriction_exemptions'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    employee_id = Column(String(50), ForeignKey('employees.employee_id'), nullable=False, index=True)
    instrument_id = Column(String(50), nullable=False)
    reason = Column(Text, nullable=False)
    expires_at = Column(UTCDateTime(), nullable=False)
    status = Column(String(20), nullable=False, default='PENDING')
    requested_by = Column(String(100), nullable=False)
    requested_by_id = Column(String(100), nullable=False)
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(UTCDateTime(), nullable=True)
    approval_notes = Column(Text, nullable=True)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class RestrictionAudit(Base):
    __tablename__ = 'restriction_audit'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    target_type = Column(String(20), nullable=False)
    target_id = Column(Uuid, nullable=False, index=True)
    action = Column(String(40), nullable=False)
    actor = Column(String(100), nullable=False)
    details = Column(J(), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class PolicyInput(Input):
    name: str = Field(min_length=1, max_length=100)
    instrument_id: str = Field(pattern=r'^[A-Z0-9.\-]{1,50}$')
    action: Literal['BLOCK', 'REVIEW']
    reason: str = Field(min_length=1, max_length=4000)
    effective_from: AwareDatetime
    effective_to: Optional[AwareDatetime] = None
    department: Optional[str] = Field(None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid_window(self):
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError('effective_to must be after effective_from')
        return self


class CheckInput(Input):
    employee_id: str = Field(min_length=1, max_length=50)
    instrument_id: str = Field(pattern=r'^[A-Z0-9.\-]{1,50}$')
    side: Literal['BUY', 'SELL']
    quantity: int = Field(gt=0, le=2147483647)
    price: Decimal = Field(gt=0, max_digits=20, decimal_places=8, allow_inf_nan=False)


class ExemptionInput(Input):
    employee_id: str = Field(min_length=1, max_length=50)
    instrument_id: str = Field(pattern=r'^[A-Z0-9.\-]{1,50}$')
    reason: str = Field(min_length=1, max_length=4000)
    expires_at: AwareDatetime


class ApprovalInput(Input):
    notes: str = Field(min_length=1, max_length=4000)


def row(obj):
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


def snapshot(obj):
    return {key: (str(value) if isinstance(value, (uuid.UUID, Decimal)) else value.isoformat() if isinstance(value, datetime) else value)
            for key, value in row(obj).items()}


async def audit(db, target, action, actor, details):
    db.add(RestrictionAudit(target_type=type(target).__name__.upper(), target_id=target.id, action=action, actor=actor, details=details))
    await db.flush()


async def employee_for(db, employee_id):
    employee = await db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, 'Employee not found')
    return employee


async def create_policy(db, data, actor):
    # Serialize first-version races as well as updates; a unique constraint is the backstop.
    if db.bind.dialect.name == 'postgresql':
        lock_key = int.from_bytes(hashlib.sha256(('policy:' + data.name).encode()).digest()[:8], 'big', signed=True)
        await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key})
    policies = (await db.scalars(select(Policy).where(Policy.name == data.name).order_by(Policy.version.desc()).with_for_update())).all()
    version = policies[0].version + 1 if policies else 1
    for previous in policies:
        if previous.status == 'ACTIVE':
            previous.status = 'RETIRED'
            await audit(db, previous, 'RETIRED', actor, {'replacement_version': version})
    policy = Policy(**data.model_dump(), version=version, status='ACTIVE', created_by=actor)
    db.add(policy)
    await db.flush()
    await audit(db, policy, 'CREATED', actor, snapshot(policy))
    return policy


async def check_permission(db, data, actor, at=None):
    at = at or now()
    employee = await employee_for(db, data.employee_id)
    policies = (await db.scalars(select(Policy).where(Policy.instrument_id == data.instrument_id, Policy.status == 'ACTIVE',
                                                     Policy.effective_from <= at).order_by(Policy.name, Policy.version))).all()
    policies = [p for p in policies if (not p.effective_to or p.effective_to > at) and (not p.department or p.department == employee.department)]
    exemption = await db.scalar(select(Exemption).where(Exemption.employee_id == employee.employee_id,
                                                       Exemption.instrument_id == data.instrument_id, Exemption.status == 'APPROVED',
                                                       Exemption.expires_at > at, Exemption.approved_at <= at).order_by(Exemption.expires_at.desc(), Exemption.id))
    reasons = [f'{p.name} v{p.version}: {p.reason}' for p in policies]
    outcome = 'BLOCK' if any(p.action == 'BLOCK' for p in policies) else 'REVIEW' if policies else 'ALLOW'
    if exemption and policies:
        outcome = 'ALLOW'
        reasons.append(f'Approved exemption {exemption.id}: {exemption.reason}')
    if employee.status != 'ACTIVE':
        outcome = 'BLOCK'
        reasons.append('Employee is inactive; exemptions cannot override inactive employee status')
        exemption = None
    if not reasons:
        reasons = ['No effective restriction applies to this employee and instrument']
    decision = Decision(**data.model_dump(), decision=outcome, reasons=reasons,
                        policy_ids=[str(p.id) for p in policies], policy_versions=[p.version for p in policies],
                        policy_snapshot=[snapshot(p) for p in policies], employee_snapshot={'employee_id': employee.employee_id, 'department': employee.department, 'status': employee.status},
                        exemption_id=exemption.id if exemption and policies else None, requester=actor, created_at=at)
    db.add(decision)
    await db.flush()
    await audit(db, decision, 'CHECKED', actor, {'decision': outcome, 'policy_ids': decision.policy_ids, 'reasons': reasons})
    if outcome in ('BLOCK', 'REVIEW'):
        from app.platform import record_signal
        await record_signal(db, domain='RESTRICTION', rule='PRETRADE_RESTRICTION_V1', fingerprint=str(decision.id),
                            severity='HIGH' if outcome == 'BLOCK' else 'MEDIUM', title=f'{outcome}: {employee.employee_id} / {data.instrument_id}',
                            explanation='; '.join(reasons), evidence={'employees': [employee.employee_id], 'decisions': [str(decision.id)], 'policy_ids': decision.policy_ids})
    return decision


async def request_exemption(db, data, user):
    employee = await employee_for(db, data.employee_id)
    if employee.status != 'ACTIVE':
        raise HTTPException(409, 'Inactive employee cannot receive an exemption')
    if data.expires_at <= now():
        raise HTTPException(422, 'Exemption expiry must be in the future')
    exemption = Exemption(**data.model_dump(), requested_by=user.username, requested_by_id=str(user.id), status='PENDING')
    db.add(exemption)
    await db.flush()
    await audit(db, exemption, 'REQUESTED', user.username, snapshot(exemption))
    return exemption


async def approve_exemption(db, exemption_id, notes, user):
    exemption = await db.scalar(select(Exemption).where(Exemption.id == exemption_id).with_for_update())
    if not exemption:
        raise HTTPException(404, 'Exemption not found')
    if exemption.requested_by_id == str(user.id):
        raise HTTPException(403, 'Requester cannot approve their own exemption')
    if exemption.status != 'PENDING':
        raise HTTPException(409, 'Exemption has already been decided')
    if exemption.expires_at <= now():
        raise HTTPException(409, 'Expired exemption cannot be approved')
    employee = await employee_for(db, exemption.employee_id)
    if employee.status != 'ACTIVE':
        raise HTTPException(409, 'Inactive employee cannot receive an exemption')
    exemption.status, exemption.approved_by, exemption.approved_at, exemption.approval_notes = 'APPROVED', user.username, now(), notes
    await audit(db, exemption, 'APPROVED', user.username, {'notes': notes, 'requester': exemption.requested_by, 'expires_at': exemption.expires_at.isoformat()})
    return exemption


router = APIRouter(prefix='/api/restrictions', tags=['restrictions'], dependencies=[Depends(require_reader)])


async def listing(db, model, limit, offset):
    return [row(x) for x in (await db.scalars(select(model).order_by(model.created_at.desc(), model.id).limit(limit).offset(offset))).all()]


@router.get('/policies')
async def policies(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, Policy, limit, offset)


@router.post('/policies')
async def policy_create(data: PolicyInput, db: AsyncSession = Depends(get_db), user=Depends(require_admin)):
    policy = await create_policy(db, data, user.username)
    await db.commit()
    return row(policy)


@router.post('/check')
async def permission_check(data: CheckInput, db: AsyncSession = Depends(get_db), user=Depends(require_analyst)):
    decision = await check_permission(db, data, user.username)
    await db.commit()
    return row(decision)


@router.get('/decisions')
async def decisions(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, Decision, limit, offset)


@router.post('/exemptions')
async def exemption_create(data: ExemptionInput, db: AsyncSession = Depends(get_db), user=Depends(require_analyst)):
    exemption = await request_exemption(db, data, user)
    await db.commit()
    return row(exemption)


@router.get('/exemptions')
async def exemptions(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, Exemption, limit, offset)


@router.post('/exemptions/{exemption_id}/approve')
async def exemption_approve(exemption_id: uuid.UUID, data: ApprovalInput, db: AsyncSession = Depends(get_db), user=Depends(require_admin)):
    exemption = await approve_exemption(db, exemption_id, data.notes, user)
    await db.commit()
    return row(exemption)


@router.get('/audit')
async def audit_rows(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, RestrictionAudit, limit, offset)
