"""Synthetic broker normalization, immutable provenance, reconciliation and CSV audit."""
import csv
import hashlib
import io
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, AwareDatetime
from sqlalchemy import Column, String, Text, Integer, Uuid, ForeignKey, UniqueConstraint, JSON, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.security import require_reader, require_analyst, require_admin
from app.models.models import Base, UTCDateTime, TradingEvent
from app.models.schemas import TradingEventCreate
from app.services.ingestion import IngestionService


def now():
    return datetime.now(timezone.utc)


def J():
    return JSON().with_variant(JSONB, 'postgresql')


class Security(Base):
    __tablename__ = 'securities'
    instrument_id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    isin = Column(String(12), unique=True, nullable=False)
    exchange = Column(String(50), nullable=False)
    currency = Column(String(3), nullable=False)
    asset_class = Column(String(50), nullable=False)


class FeedBatch(Base):
    __tablename__ = 'feed_batches'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    source = Column(String(10), nullable=False)
    received = Column(Integer, nullable=False, default=0)
    normalized = Column(Integer, nullable=False, default=0)
    rejected = Column(Integer, nullable=False, default=0)
    duplicates = Column(Integer, nullable=False, default=0)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class BrokerRecord(Base):
    __tablename__ = 'broker_records'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id = Column(Uuid, ForeignKey('feed_batches.id'), nullable=False)
    source = Column(String(10), nullable=False)
    source_event_key = Column(String(100), nullable=False)
    execution_reference = Column(String(100), nullable=True, index=True)
    raw_payload = Column(J(), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    normalized_event_id = Column(Uuid, ForeignKey('trading_events.event_id'), nullable=True)
    status = Column(String(20), nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(UTCDateTime(), nullable=False, default=now)
    __table_args__ = (UniqueConstraint('source', 'source_event_key', name='uq_broker_source_key'),)


class DataQualityIssue(Base):
    __tablename__ = 'data_quality_issues'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id = Column(Uuid, ForeignKey('feed_batches.id'), nullable=False)
    source = Column(String(10), nullable=False)
    source_event_key = Column(String(100), nullable=True)
    reason = Column(Text, nullable=False)
    payload = Column(J(), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class ReconciliationRun(Base):
    __tablename__ = 'reconciliation_runs'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    status = Column(String(20), nullable=False)
    compared = Column(Integer, nullable=False)
    matched = Column(Integer, nullable=False)
    missing = Column(Integer, nullable=False)
    duplicates = Column(Integer, nullable=False)
    mismatched = Column(Integer, nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class ReconciliationIssue(Base):
    __tablename__ = 'reconciliation_issues'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id = Column(Uuid, ForeignKey('reconciliation_runs.id'), nullable=False)
    execution_reference = Column(String(100), nullable=False)
    kind = Column(String(20), nullable=False)
    explanation = Column(Text, nullable=False)
    evidence = Column(J(), nullable=False)


class ReportExport(Base):
    __tablename__ = 'report_exports'
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    format = Column(String(50), nullable=False)
    version = Column(String(20), nullable=False)
    row_count = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    requester = Column(String(100), nullable=False)
    filters = Column(J(), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=now)


class SecurityInput(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=50, pattern=r'^[A-Z0-9.\-]+$')
    name: str = Field(min_length=1, max_length=200)
    isin: str = Field(pattern=r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')
    exchange: str = Field(min_length=1, max_length=50)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    asset_class: str = Field(min_length=1, max_length=50)


class FeedInput(BaseModel):
    records: list[dict] = Field(min_length=1, max_length=1000)


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_key(source, payload):
    if not isinstance(payload, dict):
        raise ValueError('Broker payload must be an object')
    key = payload.get('event_ref' if source == 'ALPHA' else 'messageId')
    if not isinstance(key, str) or not key.strip() or len(key) > 100:
        raise ValueError('Broker event key must be a nonempty string of at most 100 characters')
    return key


def normalize_broker_record(source: str, payload: dict) -> TradingEventCreate:
    """ALPHA uses flat FIX-like names; BETA uses nested instrument/party/trade data."""
    key = source_key(source, payload)
    if source == 'ALPHA':
        ref = payload.get('execution_ref')
        values = dict(event_type=payload['type'], timestamp=payload['time'], instrument_id=payload['symbol'],
                      trader_id=payload['trader'], account_id=payload['account'], firm_id=payload['firm'],
                      side={'B': 'BUY', 'S': 'SELL'}[payload['side']], quantity=payload['qty'],
                      price=payload['px'], order_id=payload['order_ref'])
    elif source == 'BETA':
        for field in ('trade', 'party', 'instrument'):
            if not isinstance(payload.get(field), dict):
                raise ValueError(f'BETA {field} must be an object')
        t, p = payload['trade'], payload['party']
        ref = t.get('executionReference')
        values = dict(event_type={'NEW': 'ORDER', 'FILL': 'EXECUTION', 'CXL': 'CANCEL', 'AMEND': 'MODIFY'}[payload['messageType']],
                      timestamp=payload['occurredAt'], instrument_id=payload['instrument']['ticker'],
                      trader_id=p['traderId'], account_id=p['accountId'], firm_id=p['firmId'],
                      side=t['direction'], quantity=t['units'], price=t['unitPrice'], order_id=t['orderId'])
    else:
        raise ValueError('Unsupported broker source')
    if ref is not None and (not isinstance(ref, str) or not ref.strip() or len(ref) > 100):
        raise ValueError('Execution reference must be a nonempty string of at most 100 characters')
    if values['event_type'] == 'EXECUTION' and not ref:
        raise ValueError('Execution reference required for reconciliation')
    if not isinstance(values['instrument_id'], str):
        raise ValueError('Instrument symbol must be a string')
    values['instrument_id'] = values['instrument_id'].upper()
    return TradingEventCreate(**values, event_id=uuid.uuid5(uuid.NAMESPACE_URL, f'sentinel:broker:{source}:{key}'),
                              metadata={'source': source, 'source_event_key': key, 'execution_reference': ref, 'synthetic': True})


class CanonicalKeyConflict(Exception):
    """The canonical UUID was claimed while the source key was being ingested."""


async def _existing_record(db, source, key):
    return await db.scalar(select(BrokerRecord).where(BrokerRecord.source == source, BrokerRecord.source_event_key == key))


async def _reject_record(db, batch, source, key, payload, raw_hash, reason):
    db.add(DataQualityIssue(batch_id=batch.id, source=source, source_event_key=key, reason=reason[:2000], payload=payload))
    await db.flush()
    if key and not await _existing_record(db, source, key):
        try:
            async with db.begin_nested():
                db.add(BrokerRecord(batch_id=batch.id, source=source, source_event_key=key, raw_payload=payload,
                                    content_sha256=raw_hash, status='REJECTED', error=reason[:2000]))
                await db.flush()
        except IntegrityError:
            # Another transaction may preserve the same source key while this one validates.
            # Only that known uniqueness race is recoverable; never mask other DB failures.
            if not await _existing_record(db, source, key):
                raise
    batch.rejected += 1


async def ingest_feed(db, source, records):
    batch = FeedBatch(source=source, received=len(records), normalized=0, rejected=0, duplicates=0)
    db.add(batch)
    await db.flush()
    for payload in records:
        key, raw_hash = None, None
        preserved = payload
        # Catch input failures only around parsing/normalization, never around infrastructure.
        try:
            key = source_key(source, payload)
            raw_hash = digest(payload)
        except (KeyError, ValueError, TypeError) as exc:
            try:
                raw_hash = digest(payload)
            except (ValueError, TypeError):
                # NaN/Infinity/non-JSON objects cannot be stored as PostgreSQL JSONB.
                # Preserve their explicit representation rather than silently changing values.
                preserved = {'_invalid_json_repr': repr(payload)}
                raw_hash = digest(preserved)
            await _reject_record(db, batch, source, key, preserved, raw_hash, 'Invalid broker JSON or source key: ' + str(exc))
            await db.flush()
            continue
        if db.bind.dialect.name == 'postgresql':
            lock_key = int.from_bytes(hashlib.sha256(f'broker:{source}:{key}'.encode()).digest()[:8], 'big', signed=True)
            await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key})
        existing = await _existing_record(db, source, key)
        if existing:
            if existing.content_sha256 == raw_hash:
                batch.duplicates += 1
            else:
                await _reject_record(db, batch, source, key, preserved, raw_hash, 'Source key conflicts with previously preserved payload')
            continue
        try:
            event = normalize_broker_record(source, payload)
        except (KeyError, ValueError, TypeError) as exc:
            await _reject_record(db, batch, source, key, preserved, raw_hash, str(exc))
            await db.flush()
            continue
        security = await db.get(Security, event.instrument_id)
        if security:
            event.metadata['security'] = row(security)
        try:
            # Canonical event, outbox and broker provenance must succeed or roll back together.
            async with db.begin_nested():
                try:
                    saved = (await IngestionService().ingest_batch(db, [event], commit=False))[0]
                except ValueError as exc:
                    if str(exc) != 'event_id already exists with different content':
                        raise
                    raise CanonicalKeyConflict() from exc
                db.add(BrokerRecord(batch_id=batch.id, source=source, source_event_key=key,
                                    execution_reference=event.metadata['execution_reference'], raw_payload=preserved,
                                    content_sha256=raw_hash, normalized_event_id=saved.event_id, status='NORMALIZED'))
                await db.flush()
        except (IntegrityError, CanonicalKeyConflict):
            existing = await _existing_record(db, source, key)
            if not existing:
                raise
            if existing.content_sha256 == raw_hash:
                batch.duplicates += 1
            else:
                await _reject_record(db, batch, source, key, preserved, raw_hash, 'Source key conflicts with concurrently preserved payload')
        else:
            batch.normalized += 1
        await db.flush()
    return {'batch_id': str(batch.id), 'received': batch.received, 'normalized': batch.normalized,
            'rejected': batch.rejected, 'duplicates': batch.duplicates}


def simulated_records(source, seed):
    rng = random.Random(seed)
    records = []
    for i in range(6):
        ref, key = f'SYN-{seed}-{i}', f'SYN-{seed}-{i}'
        qty, price = rng.randint(1, 20) * 100, str(Decimal('100.00') + Decimal(rng.randint(1, 100)) / 100)
        # Paired feeds intentionally exercise mismatched, missing and duplicate execution references.
        if source == 'BETA' and i == 5:
            continue
        if source == 'BETA' and i == 3:
            qty += 100
        values = dict(event_ref=key, execution_ref=ref, type='EXECUTION', time=(datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc) + timedelta(seconds=i*60)).isoformat(),
                      symbol='AAPL', trader='SYN-TRADER', account='SYN-ACCOUNT', firm='SYN-FIRM', side='B', qty=qty, px=price, order_ref=key)
        if source == 'ALPHA':
            records.append(values)
        else:
            records.append(dict(messageId=key, messageType='FILL', occurredAt=values['time'], instrument={'ticker': 'AAPL'},
                                party={'traderId': 'SYN-TRADER', 'accountId': 'SYN-ACCOUNT', 'firmId': 'SYN-FIRM'},
                                trade={'executionReference': ref, 'direction': 'BUY', 'units': qty, 'unitPrice': price, 'orderId': key}))
    if source == 'BETA':
        duplicate = json.loads(json.dumps(records[2]))
        duplicate['messageId'] += '-DUP'
        records.append(duplicate)
    return records


def row(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


async def reconcile(db):
    records = (await db.execute(select(BrokerRecord, TradingEvent).join(TradingEvent, BrokerRecord.normalized_event_id == TradingEvent.event_id)
                                .where(TradingEvent.event_type == 'EXECUTION'))).all()
    groups = {}
    for record, event in records:
        groups.setdefault(record.execution_reference, {}).setdefault(record.source, []).append((record, event))
    run = ReconciliationRun(status='CLEAN', compared=len(groups), matched=0, missing=0, duplicates=0, mismatched=0)
    db.add(run)
    await db.flush()
    for ref, sources in sorted(groups.items()):
        findings = []
        if set(sources) != {'ALPHA', 'BETA'}:
            run.missing += 1
            findings.append(('MISSING', 'Execution reference is absent from ' + ', '.join(sorted({'ALPHA', 'BETA'} - set(sources)))))
        if any(len(items) > 1 for items in sources.values()):
            run.duplicates += 1
            findings.append(('DUPLICATE', 'Multiple source records share an execution reference within a broker'))
        signatures = {(e.instrument_id, e.side, e.quantity, e.price, e.timestamp, e.account_id, e.firm_id) for items in sources.values() for _, e in items}
        if len(sources) == 2 and len(signatures) > 1:
            run.mismatched += 1
            findings.append(('MISMATCH', 'Instrument, side, quantity, price, timestamp, account or firm differs across broker records'))
        if not findings:
            run.matched += 1
        for kind, explanation in findings:
            evidence = {'events': [str(e.event_id) for items in sources.values() for _, e in items],
                        'broker_records': [str(r.id) for items in sources.values() for r, _ in items]}
            issue = ReconciliationIssue(run_id=run.id, execution_reference=ref, kind=kind, explanation=explanation, evidence=evidence)
            db.add(issue)
            await db.flush()
            from app.platform import record_signal
            await record_signal(db, domain='DATA', rule=f'RECONCILIATION_{kind}_V1',
                                fingerprint=digest({'ref': ref, 'kind': kind, 'events': sorted(evidence['events'])}),
                                severity='MEDIUM', title=f'{kind.title()} execution {ref}', explanation=explanation,
                                evidence={**evidence, 'reconciliations': [str(run.id)]})
    if run.missing or run.duplicates or run.mismatched:
        run.status = 'ISSUES'
    await db.flush()
    return run


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value


async def export_trades(db, requester, from_time=None, to_time=None):
    if from_time and to_time and from_time > to_time:
        raise HTTPException(422, 'from_time must precede to_time')
    query = select(TradingEvent).where(TradingEvent.event_type == 'EXECUTION')
    if from_time:
        query = query.where(TradingEvent.timestamp >= from_time)
    if to_time:
        query = query.where(TradingEvent.timestamp <= to_time)
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    columns = ['event_id', 'timestamp', 'instrument_id', 'trader_id', 'account_id', 'firm_id', 'side', 'quantity', 'price', 'order_id']
    writer.writerow(columns)
    count = 0
    stream = await db.stream_scalars(query.order_by(TradingEvent.timestamp, TradingEvent.event_id))
    async for event in stream:
        values = {**row(event), 'timestamp': event.timestamp.astimezone(timezone.utc).isoformat(), 'price': format(event.price, '.8f')}
        writer.writerow([safe_cell(values[key]) for key in columns])
        count += 1
    content = output.getvalue().encode('utf-8')
    db.add(ReportExport(format='SYNTHETIC_TRADES_CSV', version='1', row_count=count,
                        sha256=hashlib.sha256(content).hexdigest(), requester=requester,
                        filters={'from_time': from_time.isoformat() if from_time else None, 'to_time': to_time.isoformat() if to_time else None}))
    await db.flush()
    return content


router = APIRouter(tags=['data'], dependencies=[Depends(require_reader)])


async def listing(db, model, limit, offset):
    query = select(model)
    order = model.instrument_id if model is Security else model.created_at.desc()
    return [row(x) for x in (await db.scalars(query.order_by(order).limit(limit).offset(offset))).all()]


@router.get('/api/data/securities')
async def securities(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, Security, limit, offset)


@router.post('/api/data/securities')
async def add_security(data: SecurityInput, db: AsyncSession = Depends(get_db), user=Depends(require_admin)):
    if await db.get(Security, data.instrument_id) or await db.scalar(select(Security).where(Security.isin == data.isin)):
        raise HTTPException(409, 'Security or ISIN already exists; immutable master entries cannot be overwritten')
    entry = Security(**data.model_dump())
    db.add(entry)
    await db.commit()
    return row(entry)


@router.post('/api/data/feeds/{source}/ingest')
async def ingest(source: Literal['ALPHA', 'BETA'], data: FeedInput, db: AsyncSession = Depends(get_db), user=Depends(require_analyst)):
    result = await ingest_feed(db, source, data.records)
    await db.commit()
    return result


@router.post('/api/data/feeds/{source}/simulate')
async def simulate(source: Literal['ALPHA', 'BETA'], seed: int = Query(42, ge=0, le=2147483647), db: AsyncSession = Depends(get_db), user=Depends(require_analyst)):
    result = await ingest_feed(db, source, simulated_records(source, seed))
    await db.commit()
    return result


@router.get('/api/data/batches')
async def batches(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, FeedBatch, limit, offset)


@router.get('/api/data/quality')
async def quality(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, DataQualityIssue, limit, offset)


@router.post('/api/data/reconcile')
async def run_reconciliation(db: AsyncSession = Depends(get_db), user=Depends(require_analyst)):
    run = await reconcile(db)
    await db.commit()
    return await reconciliation_detail(run.id, db)


@router.get('/api/data/reconciliations')
async def reconciliations(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, ReconciliationRun, limit, offset)


@router.get('/api/data/reconciliations/{run_id}')
async def reconciliation_detail(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(404, 'Reconciliation not found')
    issues = (await db.scalars(select(ReconciliationIssue).where(ReconciliationIssue.run_id == run_id).order_by(ReconciliationIssue.execution_reference, ReconciliationIssue.kind))).all()
    return {**row(run), 'issues': [row(x) for x in issues]}


@router.get('/api/reports/trades.csv')
async def report(from_time: Optional[AwareDatetime] = None, to_time: Optional[AwareDatetime] = None,
                 db: AsyncSession = Depends(get_db), user=Depends(require_analyst)):
    content = await export_trades(db, user.username, from_time, to_time)
    await db.commit()
    return Response(content, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="synthetic-trades-v1.csv"'})


@router.get('/api/reports/exports')
async def exports(db: AsyncSession = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return await listing(db, ReportExport, limit, offset)
