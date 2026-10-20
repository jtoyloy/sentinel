"""Persistent synthetic HR feeds, temporal profiles and account identity resolution."""
from datetime import datetime, timedelta, timezone
import random
import uuid
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import CheckConstraint, Column, ForeignKey, JSON, String, UniqueConstraint, Uuid, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_reader, require_analyst
from app.models.models import Base, UTCDateTime


def now():
    return datetime.now(timezone.utc)


class Employee(Base):
    __tablename__ = "employees"
    employee_id = Column(String(50), primary_key=True)
    full_name = Column(String(200), nullable=False)
    email = Column(String(254), nullable=False, unique=True)
    department = Column(String(100), nullable=False)
    role = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    manager_id = Column(String(50), nullable=True)
    updated_at = Column(UTCDateTime(), nullable=False)
    __table_args__ = (CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="employee_status"),)


class EmployeeAccount(Base):
    __tablename__ = "employee_accounts"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(String(50), ForeignKey("employees.employee_id"), nullable=False, index=True)
    firm_id = Column(String(50), nullable=False)
    account_id = Column(String(50), nullable=False)
    __table_args__ = (UniqueConstraint("firm_id", "account_id", name="employee_firm_account"),)


class EmployeeChange(Base):
    __tablename__ = "employee_changes"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(String(50), ForeignKey("employees.employee_id"), nullable=False, index=True)
    effective_at = Column(UTCDateTime(), nullable=False)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    source_key = Column(String(200), nullable=False, unique=True)


class FeedIssue(Base):
    __tablename__ = "employee_feed_issues"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(50), nullable=False, default="HR")
    source_key = Column(String(200), nullable=True)
    reason = Column(String(2000), nullable=False)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


BoundedID = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^\S+$")]


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    firm_id: BoundedID
    account_id: BoundedID


class EmployeeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=200, pattern=r"\S")
    employee_id: BoundedID
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    department: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    status: Literal["ACTIVE", "INACTIVE"]
    effective_at: AwareDatetime
    manager_id: Optional[BoundedID] = None
    accounts: list[AccountInput] = Field(default_factory=list, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.casefold().strip()
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("email must contain a single @ and no whitespace")
        local, domain = value.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("invalid email address")
        return value

    @field_validator("accounts")
    @classmethod
    def unique_accounts(cls, value):
        keys = [(a.firm_id, a.account_id) for a in value]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate firm/account mapping within HR record")
        return value


class FeedInput(BaseModel):
    # Validation is per record so one corrupt HR record cannot discard a feed.
    records: list[dict] = Field(min_length=1, max_length=1000)


def employee_dict(row, account_count=None):
    value = {key: getattr(row, key) for key in ("employee_id", "full_name", "email", "department", "role", "status", "manager_id", "updated_at")}
    if account_count is not None:
        value["account_count"] = account_count
    return value


def issue_dict(row):
    return {key: getattr(row, key) for key in ("id", "source", "source_key", "reason", "payload", "created_at")}


async def ingest_hr_feed(db: AsyncSession, records: list[dict]):
    accepted = rejected = duplicates = 0
    issues = []
    for raw in records:
        reason = None
        try:
            record = EmployeeRecord.model_validate(raw)
            payload = record.model_dump(mode="json")
            previous = await db.scalar(select(EmployeeChange).where(EmployeeChange.source_key == record.source_key))
            if previous:
                if previous.payload == payload:
                    duplicates += 1
                    continue
                raise ValueError("source_key reused with different HR content")
            async with db.begin_nested():
                employee = await db.scalar(select(Employee).where(Employee.employee_id == record.employee_id).with_for_update())
                email_owner = await db.scalar(select(Employee).where(Employee.email == record.email, Employee.employee_id != record.employee_id))
                if email_owner:
                    raise ValueError("email already belongs to another employee")
                if record.manager_id == record.employee_id:
                    raise ValueError("employee cannot manage themselves")
                # Reject conflicts even for historical records, preserving unambiguous identity.
                for account in record.accounts:
                    owner = await db.scalar(select(EmployeeAccount).where(EmployeeAccount.firm_id == account.firm_id, EmployeeAccount.account_id == account.account_id))
                    if owner and owner.employee_id != record.employee_id:
                        raise ValueError(f"account {account.firm_id}/{account.account_id} belongs to another employee")
                if employee is None:
                    employee = Employee(employee_id=record.employee_id)
                    db.add(employee)
                if employee.updated_at is None or record.effective_at >= employee.updated_at:
                    for key in ("full_name", "email", "department", "role", "status", "manager_id"):
                        setattr(employee, key, getattr(record, key))
                    employee.updated_at = record.effective_at
                    await db.flush()
                    existing = list((await db.scalars(select(EmployeeAccount).where(EmployeeAccount.employee_id == employee.employee_id))).all())
                    desired = {(a.firm_id, a.account_id) for a in record.accounts}
                    current = {(a.firm_id, a.account_id) for a in existing}
                    for account in existing:
                        if (account.firm_id, account.account_id) not in desired:
                            await db.delete(account)
                    for firm_id, account_id in desired - current:
                        db.add(EmployeeAccount(employee_id=employee.employee_id, firm_id=firm_id, account_id=account_id))
                db.add(EmployeeChange(employee_id=record.employee_id, effective_at=record.effective_at, payload=payload, source_key=record.source_key))
                await db.flush()
            accepted += 1
        except (ValidationError, ValueError) as exc:
            reason = str(exc)[:2000]
        except IntegrityError:
            reason = "Conflicting employee identity, account, or source key; record rejected"
        if reason:
            issue = FeedIssue(source="HR", source_key=str(raw.get("source_key", ""))[:200] or None, reason=reason, payload=raw)
            db.add(issue)
            await db.flush()
            rejected += 1
            issues.append(issue_dict(issue))
    return {"accepted": accepted, "rejected": rejected, "duplicates": duplicates, "issues": issues}


async def resolve_employee(db: AsyncSession, *, email=None, account_id=None, firm_id=None, trader_id=None):
    """Resolve provided identities together; contradictory or ambiguous matches fail closed."""
    matches = set()
    if email:
        matches.update((await db.scalars(select(Employee.employee_id).where(Employee.email == email.strip().casefold()))).all())
    if trader_id:
        matches.update((await db.scalars(select(Employee.employee_id).where(Employee.employee_id == trader_id))).all())
    if account_id:
        query = select(EmployeeAccount.employee_id).where(EmployeeAccount.account_id == account_id)
        if firm_id:
            query = query.where(EmployeeAccount.firm_id == firm_id)
        matches.update((await db.scalars(query)).all())
    if len(matches) > 1:
        raise HTTPException(409, "Ambiguous employee identity")
    return await db.get(Employee, next(iter(matches))) if matches else None


async def enrich_event(db: AsyncSession, event_data: dict):
    employee = await resolve_employee(db, account_id=event_data.get("account_id"), firm_id=event_data.get("firm_id"), trader_id=event_data.get("trader_id"))
    return {"employee_id": employee.employee_id, "department": employee.department, "role": employee.role} if employee else {}


router = APIRouter(prefix="/api/employees", tags=["employees"], dependencies=[Depends(require_reader)])


@router.get("")
async def list_employees(search: Optional[str] = Query(None, max_length=200), department: Optional[str] = Query(None, max_length=100), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    count = select(func.count(EmployeeAccount.id)).where(EmployeeAccount.employee_id == Employee.employee_id).correlate(Employee).scalar_subquery()
    query = select(Employee, count)
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(Employee.full_name.ilike(pattern), Employee.email.ilike(pattern), Employee.employee_id.ilike(pattern)))
    if department:
        query = query.where(Employee.department == department)
    rows = (await db.execute(query.order_by(Employee.employee_id).limit(limit).offset(offset))).all()
    return [employee_dict(row, n) for row, n in rows]


@router.get("/quality")
async def list_quality(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(FeedIssue).order_by(FeedIssue.created_at.desc(), FeedIssue.id).limit(limit).offset(offset))).all()
    return [issue_dict(row) for row in rows]


@router.get("/resolve")
async def resolve_identity(identity: str = Query(min_length=1, max_length=254), db: AsyncSession = Depends(get_db)):
    employee = await resolve_employee(db, email=identity, account_id=identity, trader_id=identity)
    if not employee:
        raise HTTPException(404, "Employee identity not found")
    match_type = "EMAIL" if employee.email == identity.strip().casefold() else "EMPLOYEE_ID" if employee.employee_id == identity else "ACCOUNT"
    return {"employee_id": employee.employee_id, "match_type": match_type}


@router.post("/feed", dependencies=[Depends(require_analyst)])
async def feed(body: FeedInput, db: AsyncSession = Depends(get_db)):
    result = await ingest_hr_feed(db, body.records)
    await db.commit()
    return result


def simulate_hr_records(seed: int = 42, count: int = 12):
    rng = random.Random(seed)
    departments = ["Equities", "Fixed Income", "Compliance", "Research"]
    names = ["Alex Morgan", "Sam Rivera", "Jordan Chen", "Taylor Patel", "Casey Brooks", "Riley Kim"]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"source_key": f"synthetic-hr:{seed}:{i}:v1", "employee_id": f"EMP-{seed}-{i:04d}", "full_name": names[i % len(names)] + f" {i + 1}", "email": f"employee.{seed}.{i}@sentinel.example", "department": rng.choice(departments), "role": rng.choice(["Analyst", "Trader", "Manager"]), "status": "ACTIVE", "effective_at": (base + timedelta(minutes=i)).isoformat(), "manager_id": None if i == 0 else f"EMP-{seed}-0000", "accounts": [{"firm_id": "SYNTHETIC", "account_id": f"HR-{seed}-{i:04d}"}]} for i in range(count)]


@router.post("/simulate", dependencies=[Depends(require_analyst)])
async def simulate(seed: int = Query(42, ge=0, le=2147483647), count: int = Query(12, ge=1, le=1000), db: AsyncSession = Depends(get_db)):
    result = await ingest_hr_feed(db, simulate_hr_records(seed, count))
    await db.commit()
    return result


@router.get("/{employee_id}")
async def employee_detail(employee_id: str, db: AsyncSession = Depends(get_db)):
    employee = await db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "Employee not found")
    accounts = (await db.scalars(select(EmployeeAccount).where(EmployeeAccount.employee_id == employee_id).order_by(EmployeeAccount.firm_id, EmployeeAccount.account_id))).all()
    history = (await db.scalars(select(EmployeeChange).where(EmployeeChange.employee_id == employee_id).order_by(EmployeeChange.effective_at, EmployeeChange.source_key))).all()
    return {**employee_dict(employee, len(accounts)), "accounts": [{"id": a.id, "firm_id": a.firm_id, "account_id": a.account_id} for a in accounts], "history": [{"id": h.id, "effective_at": h.effective_at, "source_key": h.source_key, "payload": h.payload} for h in history]}
