"""Shared signals, cross-domain investigation and operational APIs.

Domain model imports occur only in register_domains to avoid circular service imports.
"""
import asyncio
from contextlib import suppress
import hashlib
import importlib
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, Text, JSON, Uuid, ForeignKey, select, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.redis import redis_client
from app.core.security import require_reader, require_analyst, require_admin
from app.models.models import Base, UTCDateTime, Alert, TradingEvent, EventOutbox, StreamCursor


def now():
    return datetime.now(timezone.utc)


json_type = JSON().with_variant(JSONB, "postgresql")


class ComplianceSignal(Base):
    __tablename__ = "compliance_signals"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    domain = Column(String(30), nullable=False, index=True)
    rule = Column(String(80), nullable=False)
    fingerprint = Column(String(64), nullable=False, unique=True)
    severity = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="OPEN", index=True)
    title = Column(String(300), nullable=False)
    explanation = Column(Text, nullable=False)
    evidence = Column(json_type, nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class Investigation(Base):
    __tablename__ = "investigations"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    title = Column(String(200), nullable=False)
    priority = Column(String(20), nullable=False)
    owner = Column(String(100), nullable=False)
    summary = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="OPEN")
    signal_ids = Column(json_type, nullable=False, default=list)
    alert_ids = Column(json_type, nullable=False, default=list)
    created_at = Column(UTCDateTime(), nullable=False, default=now)
    updated_at = Column(UTCDateTime(), nullable=False, default=now)


class PlatformAudit(Base):
    __tablename__ = "platform_audit"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    target_type = Column(String(40), nullable=False, index=True)
    target_id = Column(String(100), nullable=False, index=True)
    action = Column(String(40), nullable=False)
    actor = Column(String(100), nullable=False)
    notes = Column(Text, nullable=False)
    details = Column(json_type, nullable=False, default=dict)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"
    name = Column(String(50), primary_key=True)
    last_success = Column(UTCDateTime(), nullable=True)
    last_failure = Column(UTCDateTime(), nullable=True)
    processed = Column(Integer, nullable=False, default=0)
    failures = Column(Integer, nullable=False, default=0)
    last_error = Column(String(300), nullable=True)


class StreamFailure(Base):
    __tablename__ = "stream_failures"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    stream_id = Column(String(50), nullable=False, unique=True)
    payload = Column(json_type, nullable=False)
    error = Column(String(300), nullable=False)
    attempts = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="RETRYING")
    created_at = Column(UTCDateTime(), nullable=False, default=now)


async def record_signal(db, *, domain, rule, fingerprint, severity, title, explanation, evidence):
    key = hashlib.sha256((domain + ":" + rule + ":" + fingerprint).encode()).hexdigest()
    existing = await db.scalar(select(ComplianceSignal).where(ComplianceSignal.fingerprint == key))
    if existing:
        return None
    signal = ComplianceSignal(domain=domain, rule=rule, fingerprint=key, severity=severity,
                              title=title, explanation=explanation, evidence=evidence)
    try:
        async with db.begin_nested():
            db.add(signal)
            await db.flush()
    except IntegrityError:
        return None
    return signal


def serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def register_domains():
    """Explicit registration used by app startup and Alembic metadata loading."""
    return [importlib.import_module("app.domains." + name) for name in
            ("employees", "communications", "restrictions", "data")]


router = APIRouter(prefix="/api", tags=["platform"], dependencies=[Depends(require_reader)])


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    owner: Optional[str] = Field(None, min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=10000, pattern=r"\S")
    signal_ids: List[UUID] = Field(default_factory=list, max_length=100)
    alert_ids: List[UUID] = Field(default_factory=list, max_length=100)


class CaseAction(BaseModel):
    action: Literal["NOTE", "ESCALATE", "CLOSE", "REOPEN"]
    notes: str = Field(min_length=1, max_length=10000, pattern=r"\S")


class SignalUpdate(BaseModel):
    status: Literal["OPEN", "INVESTIGATING", "RESOLVED", "FALSE_POSITIVE"]
    notes: str = Field(min_length=1, max_length=4000, pattern=r"\S")


class Recovery(BaseModel):
    mode: Literal["REPUBLISH_PENDING", "REBUILD_STREAM"]


@router.get("/signals")
async def signals(domain: Optional[str] = None, status: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                  db: AsyncSession = Depends(get_db)):
    query = select(ComplianceSignal)
    if domain:
        query = query.where(ComplianceSignal.domain == domain)
    if status:
        query = query.where(ComplianceSignal.status == status)
    rows = await db.scalars(query.order_by(ComplianceSignal.created_at.desc(), ComplianceSignal.id).offset(offset).limit(limit))
    return [serialize(row) for row in rows]


@router.patch("/signals/{signal_id}")
async def change_signal(signal_id: UUID, data: SignalUpdate,
                        user=Depends(require_analyst), db: AsyncSession = Depends(get_db)):
    signal = await db.scalar(select(ComplianceSignal).where(ComplianceSignal.id == signal_id).with_for_update())
    if not signal:
        raise HTTPException(404, "Signal not found")
    old_status = signal.status
    signal.status = data.status
    db.add(PlatformAudit(target_type="SIGNAL", target_id=str(signal_id), action="DISPOSITION",
                         actor=user.username, notes=data.notes,
                         details={"old_status": old_status, "new_status": data.status}))
    await db.commit()
    return serialize(signal)


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    from app.domains.employees import Employee
    from app.domains.communications import Communication
    models = {"employees": Employee, "communications": Communication, "trade_events": TradingEvent,
              "trade_alerts": Alert, "signals": ComplianceSignal}
    result = {key: await db.scalar(select(func.count()).select_from(model)) for key, model in models.items()}
    result["open_cases"] = await db.scalar(select(func.count()).select_from(Investigation).where(
        Investigation.status != "CLOSED"))
    return result


@router.get("/investigations")
async def cases(status: Optional[str] = None, limit: int = Query(100, ge=1, le=1000),
                offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    query = select(Investigation)
    if status:
        query = query.where(Investigation.status == status)
    rows = await db.scalars(query.order_by(Investigation.updated_at.desc(), Investigation.id).offset(offset).limit(limit))
    return [serialize(row) for row in rows]


@router.post("/investigations")
async def create_case(data: CaseCreate, user=Depends(require_analyst), db: AsyncSession = Depends(get_db)):
    for model, ids in ((ComplianceSignal, data.signal_ids), (Alert, data.alert_ids)):
        if ids:
            key = model.id if model is ComplianceSignal else model.alert_id
            count = await db.scalar(select(func.count()).select_from(model).where(key.in_(set(ids))))
            if count != len(set(ids)):
                raise HTTPException(422, "Linked signal or alert does not exist")
    case = Investigation(title=data.title, priority=data.priority, owner=data.owner or user.username,
                         summary=data.summary, signal_ids=sorted(set(map(str, data.signal_ids))),
                         alert_ids=sorted(set(map(str, data.alert_ids))))
    db.add(case)
    await db.flush()
    db.add(PlatformAudit(target_type="CASE", target_id=str(case.id), action="CREATE", actor=user.username,
                         notes=data.summary, details={"priority": data.priority}))
    await db.commit()
    return serialize(case)


async def get_case(db, case_id, lock=False):
    query = select(Investigation).where(Investigation.id == case_id)
    case = await db.scalar(query.with_for_update() if lock else query)
    if not case:
        raise HTTPException(404, "Investigation not found")
    return case


@router.get("/investigations/{case_id}")
async def case_detail(case_id: UUID, db: AsyncSession = Depends(get_db)):
    case = await get_case(db, case_id)
    audits = await db.scalars(select(PlatformAudit).where(PlatformAudit.target_type == "CASE",
        PlatformAudit.target_id == str(case.id)).order_by(PlatformAudit.created_at, PlatformAudit.id))
    linked_signals = await db.scalars(select(ComplianceSignal).where(
        ComplianceSignal.id.in_([UUID(value) for value in case.signal_ids])))
    linked_alerts = await db.scalars(select(Alert).where(Alert.alert_id.in_(
        [UUID(value) for value in case.alert_ids])))
    return {**serialize(case), "timeline": [serialize(row) for row in audits],
            "signals": [serialize(row) for row in linked_signals],
            "alerts": [serialize(row) for row in linked_alerts]}


@router.post("/investigations/{case_id}/actions")
async def case_action(case_id: UUID, data: CaseAction, user=Depends(require_analyst),
                      db: AsyncSession = Depends(get_db)):
    case = await get_case(db, case_id, lock=True)
    old = case.status
    if data.action == "REOPEN" and old != "CLOSED":
        raise HTTPException(409, "Only a closed case can be reopened")
    if old == "CLOSED" and data.action != "REOPEN":
        raise HTTPException(409, "Reopen the closed case before adding actions")
    target = {"ESCALATE": "ESCALATED", "CLOSE": "CLOSED", "REOPEN": "OPEN"}
    case.status = target.get(data.action, old)
    case.updated_at = now()
    db.add(PlatformAudit(target_type="CASE", target_id=str(case_id), action=data.action,
                         actor=user.username, notes=data.notes,
                         details={"old_status": old, "new_status": case.status}))
    await db.commit()
    return serialize(case)


@router.get("/operations")
async def operations(db: AsyncSession = Depends(get_db)):
    from app.domains.employees import FeedIssue
    from app.domains.data import DataQualityIssue
    from app.services.pipeline import STREAM
    redis_ok, stream_length, stream_last_id = False, None, None
    try:
        redis_ok = bool(await redis_client.client.ping())
        stream_length = await redis_client.client.xlen(STREAM)
        latest = await redis_client.client.xrevrange(STREAM, count=1)
        stream_last_id = latest[0][0] if latest else "0-0"
    except Exception:
        pass
    cursor = await db.get(StreamCursor, "detection-v1")
    heartbeat = await db.get(WorkerHeartbeat, "detection-v1")
    worker = serialize(heartbeat) if heartbeat else None
    worker_fresh = bool(heartbeat and heartbeat.last_success and
                        (now() - heartbeat.last_success).total_seconds() < 15 and
                        (not heartbeat.last_failure or heartbeat.last_success >= heartbeat.last_failure))
    return {"postgres": True, "redis": redis_ok, "worker_healthy": worker_fresh,
            "stream_last_id": stream_last_id,
            "outbox_pending": await db.scalar(select(func.count()).select_from(EventOutbox).where(
                EventOutbox.published.is_(False))), "stream_length": stream_length,
            "cursor": cursor.last_id if cursor else "0-0", "worker": worker,
            "events": await db.scalar(select(func.count()).select_from(TradingEvent)),
            "stream_failures": [serialize(row) for row in (await db.scalars(select(StreamFailure).order_by(StreamFailure.created_at.desc()).limit(100))).all()],
            "feed_issue_count": (await db.scalar(select(func.count()).select_from(FeedIssue))) +
                                (await db.scalar(select(func.count()).select_from(DataQualityIssue)))}


@router.post("/operations/recover")
async def recover(data: Recovery, request: Request, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    lock = getattr(request.app.state, "recovery_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.recovery_lock = lock
    async with lock:
        return await _recover_locked(data, request, user, db)


async def _recover_locked(data, request, user, db):
    from app.services.pipeline import run_pipeline
    task = getattr(request.app.state, "pipeline_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    try:
        if db.bind.dialect.name == "postgresql":
            locked = await db.scalar(text("SELECT pg_try_advisory_xact_lock(73921811)"))
            if not locked:
                raise HTTPException(409, "Another process owns the worker; use a single API process for recovery")
        rows = (await db.scalars(select(EventOutbox).where(
            True if data.mode == "REBUILD_STREAM" else EventOutbox.published.is_(False)).with_for_update())).all()
        for row in rows:
            row.published = False
        if data.mode == "REBUILD_STREAM":
            cursor = await db.get(StreamCursor, "detection-v1")
            if cursor:
                cursor.last_id = "0-0"
        db.add(PlatformAudit(target_type="OPERATION", target_id="redis-stream", action=data.mode,
                             actor=user.username, notes="Requeued retained events; source evidence preserved",
                             details={"events_requeued": len(rows)}))
        await db.commit()
        return {"mode": data.mode, "events_requeued": len(rows)}
    finally:
        if task:
            request.app.state.pipeline_task = asyncio.create_task(run_pipeline(request.app.state.broadcast))
