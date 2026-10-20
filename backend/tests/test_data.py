"""Real SQLite persistence tests for broker lineage, reconciliation and safe exports."""
import csv
import hashlib
import io
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.models import Base, EventOutbox, TradingEvent
from app.domains.data import (
    BrokerRecord, DataQualityIssue, FeedBatch, ReportExport, ReconciliationIssue,
    Security, export_trades, ingest_feed, normalize_broker_record, reconcile, simulated_records,
)


@pytest_asyncio.fixture
async def db():
    # Import registration, including compliance signals used by reconciliation.
    import app.platform  # noqa: F401
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_formats_persist_events_outbox_master_and_idempotency(db):
    db.add(Security(instrument_id='AAPL', name='Synthetic Apple', isin='US0378331005', exchange='XNAS', currency='USD', asset_class='EQUITY'))
    await db.flush()
    alpha = await ingest_feed(db, 'ALPHA', simulated_records('ALPHA', 42))
    beta = await ingest_feed(db, 'BETA', simulated_records('BETA', 42))
    assert alpha['normalized'] == beta['normalized'] == 6
    assert (await ingest_feed(db, 'ALPHA', simulated_records('ALPHA', 42)))['duplicates'] == 6
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(TradingEvent)) == 12
    assert await db.scalar(select(func.count()).select_from(EventOutbox)) == 12
    record = await db.scalar(select(BrokerRecord).where(BrokerRecord.source == 'ALPHA'))
    event = await db.get(TradingEvent, record.normalized_event_id)
    assert record.content_sha256 == hashlib.sha256(__import__('json').dumps(record.raw_payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert event.event_metadata['security']['currency'] == 'USD'
    assert record.raw_payload['px'] != float(record.raw_payload['px'])


@pytest.mark.asyncio
async def test_quality_conflicts_and_invalid_records_preserve_original(db):
    original = simulated_records('ALPHA', 7)[0]
    await ingest_feed(db, 'ALPHA', [original])
    conflict = {**original, 'qty': 999}
    invalid = {**original, 'event_ref': 'BAD', 'time': '2026-10-20T14:30:00'}
    result = await ingest_feed(db, 'ALPHA', [conflict, invalid, {'no_key': True}])
    await db.commit()
    assert result['rejected'] == 3
    assert await db.scalar(select(func.count()).select_from(DataQualityIssue)) == 3
    preserved = await db.scalar(select(BrokerRecord).where(BrokerRecord.source_event_key == original['event_ref']))
    assert preserved.raw_payload == original
    rejected = await db.scalar(select(BrokerRecord).where(BrokerRecord.source_event_key == 'BAD'))
    assert rejected.status == 'REJECTED'
    assert await db.scalar(select(func.count()).select_from(TradingEvent)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('order', [('ALPHA', 'BETA'), ('BETA', 'ALPHA')])
async def test_reconciliation_missing_duplicate_mismatch_and_normal_controls(db, order):
    for source in order:
        await ingest_feed(db, source, simulated_records(source, 9))
    run = await reconcile(db)
    await db.commit()
    assert (run.compared, run.matched, run.missing, run.duplicates, run.mismatched) == (6, 3, 1, 1, 1)
    issues = (await db.scalars(select(ReconciliationIssue).where(ReconciliationIssue.run_id == run.id))).all()
    assert {issue.kind for issue in issues} == {'MISSING', 'DUPLICATE', 'MISMATCH'}
    assert all(issue.evidence['events'] for issue in issues)
    again = await reconcile(db)
    await db.commit()
    assert again.id != run.id
    from app.platform import ComplianceSignal
    assert await db.scalar(select(func.count()).select_from(ComplianceSignal)) == 3


@pytest.mark.asyncio
async def test_csv_utc_decimal_injection_filter_and_export_audit(db):
    payload = simulated_records('ALPHA', 11)[0]
    payload.update(trader='=WEBSERVICE("https://invalid")', px='101.12345678')
    await ingest_feed(db, 'ALPHA', [payload])
    content = await export_trades(db, 'investigator', datetime(2026, 10, 20, tzinfo=timezone.utc))
    await db.commit()
    parsed = list(csv.DictReader(io.StringIO(content.decode())))
    assert len(parsed) == 1
    assert parsed[0]['price'] == '101.12345678'
    assert parsed[0]['trader_id'].startswith("'=")
    assert parsed[0]['timestamp'].endswith('+00:00')
    audit = await db.scalar(select(ReportExport))
    assert audit.row_count == 1 and audit.requester == 'investigator'
    assert audit.sha256 == hashlib.sha256(content).hexdigest()
    assert list(csv.DictReader(io.StringIO((await export_trades(db, 'investigator', datetime(2030, 1, 1, tzinfo=timezone.utc))).decode()))) == []


@pytest.mark.parametrize('source', ['ALPHA', 'BETA'])
def test_simulator_deterministic_and_canonical_schema(source):
    records = simulated_records(source, 55)
    assert records == simulated_records(source, 55)
    assert records != simulated_records(source, 56)
    normalized = normalize_broker_record(source, records[0])
    assert normalized.timestamp.tzinfo and normalized.price > 0
    assert normalized.event_id == normalize_broker_record(source, records[0]).event_id


@pytest.mark.asyncio
async def test_mixed_malformed_feed_preserves_failures_and_commits_valid_peers(db):
    base = simulated_records('ALPHA', 88)[0]
    records = [base, {**base, 'event_ref': 'NULL-SYMBOL', 'symbol': None},
               {**base, 'event_ref': 'NUMBER-SYMBOL', 'symbol': 123},
               {**base, 'event_ref': 'INVALID-PRICE', 'px': 'not-money'},
               {**base, 'event_ref': 'NONFINITE', 'px': float('nan')},
               {**base, 'event_ref': 'NESTED-SIDE', 'side': []},
               {**base, 'qty': 999}, simulated_records('ALPHA', 88)[1]]
    result = await ingest_feed(db, 'ALPHA', records)
    beta = simulated_records('BETA', 88)[0]
    nested = await ingest_feed(db, 'BETA', [{**beta, 'messageId': 'BAD-TRADE', 'trade': None},
                                         {**beta, 'messageId': 'BAD-PARTY', 'party': 42}, beta])
    await db.commit()
    assert (result['normalized'], result['rejected']) == (2, 6)
    assert (nested['normalized'], nested['rejected']) == (1, 2)
    assert await db.scalar(select(func.count()).select_from(DataQualityIssue)) == 8
    assert await db.scalar(select(func.count()).select_from(EventOutbox)) == 3
    assert await db.scalar(select(func.count()).select_from(TradingEvent)) == 3
    invalid = await db.scalar(select(BrokerRecord).where(BrokerRecord.source_event_key == 'NONFINITE'))
    assert invalid.status == 'REJECTED' and 'nan' in invalid.raw_payload['_invalid_json_repr']
    preserved = await db.scalar(select(BrokerRecord).where(BrokerRecord.source_event_key == base['event_ref'], BrokerRecord.source == 'ALPHA'))
    assert preserved.raw_payload == base


@pytest.mark.asyncio
async def test_unique_key_race_preserves_winner_and_has_no_orphan_event(db, monkeypatch):
    import app.domains.data as data
    payload = simulated_records('ALPHA', 91)[0]
    await ingest_feed(db, 'ALPHA', [payload])
    await db.commit()
    original = data._existing_record
    calls = 0
    async def stale_first_lookup(session, source, key):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None  # Simulate a stale read immediately before another transaction commits.
        return await original(session, source, key)
    monkeypatch.setattr(data, '_existing_record', stale_first_lookup)
    result = await ingest_feed(db, 'ALPHA', [{**payload, 'qty': 777}, simulated_records('ALPHA', 91)[1]])
    await db.commit()
    assert (result['rejected'], result['normalized']) == (1, 1)
    assert await db.scalar(select(func.count()).select_from(TradingEvent)) == 2
    assert await db.scalar(select(func.count()).select_from(EventOutbox)) == 2
    winner = await original(db, 'ALPHA', payload['event_ref'])
    assert winner.raw_payload == payload


@pytest.mark.asyncio
async def test_canonical_failure_rolls_back_record_and_event_and_propagates(db, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services.ingestion import IngestionService
    original = IngestionService.ingest_batch
    async def unavailable(self, session, records, commit=True):
        await original(self, session, records, commit=False)
        raise OperationalError('insert', {}, RuntimeError('database unavailable'))
    monkeypatch.setattr(IngestionService, 'ingest_batch', unavailable)
    with pytest.raises(OperationalError):
        await ingest_feed(db, 'ALPHA', [simulated_records('ALPHA', 92)[0]])
    assert await db.scalar(select(func.count()).select_from(TradingEvent)) == 0
    assert await db.scalar(select(func.count()).select_from(EventOutbox)) == 0
    assert await db.scalar(select(func.count()).select_from(BrokerRecord)) == 0
    assert await db.scalar(select(func.count()).select_from(DataQualityIssue)) == 0
