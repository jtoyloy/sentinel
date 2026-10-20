"""Integrated platform persistence, trustworthy enrichment, investigations and recovery."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.main import app
from app.core.database import get_db
from app.core.security import get_current_user, Principal
from app.models.models import Base, TradingEvent, EventOutbox, StreamCursor, Alert
from app.models.schemas import TradingEventCreate
from app.services.ingestion import IngestionService
from app.platform import ComplianceSignal, Investigation, PlatformAudit, StreamFailure, record_signal
from app.domains.employees import ingest_hr_feed
from app.domains.data import Security
from app.services import pipeline


@pytest_asyncio.fixture
async def platform_db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    identity = SimpleNamespace(role='ADMIN', username='real-admin')
    async def current():
        return Principal(uuid.UUID(int=1), identity.username, identity.role)
    async def db_dependency():
        async with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        yield client,sessions,identity
    app.dependency_overrides.clear()
    await engine.dispose()


async def signal(sessions):
    async with sessions() as db:
        row = await record_signal(db, domain='COMMUNICATION',rule='TEST:v1',fingerprint='stable',severity='HIGH',
             title='Synthetic inside information',explanation='Exact test evidence',evidence={'communications':[],'events':[]})
        await db.commit()
        return row.id


@pytest.mark.asyncio
async def test_shared_signal_dedup_and_case_lifecycle(platform_db):
    client,sessions,identity=platform_db
    signal_id=await signal(sessions)
    async with sessions() as db:
        repeat=await record_signal(db,domain='COMMUNICATION',rule='TEST:v1',fingerprint='stable',severity='HIGH',
             title='Synthetic inside information',explanation='Exact test evidence',evidence={})
        assert repeat is None
        await db.commit()
    response=await client.post('/api/investigations',json={'title':'Cross-domain review','priority':'HIGH',
        'summary':'Review preserved evidence','signal_ids':[str(signal_id)],'alert_ids':[]})
    assert response.status_code==200,response.text
    case_id=response.json()['id']
    for action,status in [('ESCALATE','ESCALATED'),('CLOSE','CLOSED')]:
        response=await client.post(f'/api/investigations/{case_id}/actions',json={'action':action,'notes':'Reviewed supporting evidence'})
        assert response.status_code==200,response.text
        assert response.json()['status']==status
    assert (await client.post(f'/api/investigations/{case_id}/actions',json={'action':'NOTE','notes':'Closed note'})).status_code==409
    response=await client.post(f'/api/investigations/{case_id}/actions',json={'action':'REOPEN','notes':'Late evidence'})
    assert response.json()['status']=='OPEN'
    detail=(await client.get(f'/api/investigations/{case_id}')).json()
    assert len(detail['signals'])==1 and detail['signals'][0]['id']==str(signal_id)
    assert [entry['action'] for entry in detail['timeline']]==['CREATE','ESCALATE','CLOSE','REOPEN']
    assert all(entry['actor']=='real-admin' for entry in detail['timeline'])
    assert (await client.get('/api/overview')).json()['open_cases']==1
    changed=await client.patch(f'/api/signals/{signal_id}',json={'status':'INVESTIGATING','notes':'Case linked'})
    assert changed.json()['status']=='INVESTIGATING'
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(PlatformAudit))==5


@pytest.mark.asyncio
async def test_unknown_evidence_and_role_boundaries(platform_db):
    client,_,identity=platform_db
    bad=await client.post('/api/investigations',json={'title':'Unknown','summary':'Invalid reference',
        'signal_ids':[str(uuid.uuid4())]})
    assert bad.status_code==422
    identity.role='VIEWER'
    for path,body in [('/api/investigations',{'title':'No','summary':'Not permitted'}),
                      ('/api/operations/recover',{'mode':'REPUBLISH_PENDING'})]:
        assert (await client.post(path,json=body)).status_code==403
    assert (await client.get('/api/overview')).status_code==200
    identity.role='ANALYST'
    assert (await client.post('/api/operations/recover',json={'mode':'REBUILD_STREAM'})).status_code==403


def trade(**updates):
    return TradingEventCreate(event_id=updates.pop('event_id',uuid.uuid4()),event_type='EXECUTION',
        timestamp=datetime(2026,10,20,14,30,tzinfo=timezone.utc),instrument_id='AAPL',trader_id='TRADER',
        firm_id='FIRM',account_id=updates.pop('account_id','ACCOUNT'),side='BUY',quantity=100,price='101.25',
        order_id='ORDER',metadata=updates.pop('metadata',{}),**updates)


@pytest.mark.asyncio
async def test_trusted_enrichment_and_source_idempotency(platform_db):
    _,sessions,_=platform_db
    async with sessions() as db:
        await ingest_hr_feed(db,[{'source_key':'hr-1','employee_id':'E1','full_name':'Synthetic One','email':'one@sentinel.example',
            'department':'Research','role':'Analyst','status':'ACTIVE','effective_at':'2026-01-01T00:00:00Z',
            'accounts':[{'firm_id':'FIRM','account_id':'ACCOUNT'}]}])
        db.add(Security(instrument_id='AAPL',name='Synthetic Apple',isin='US0378331005',exchange='NASDAQ',currency='USD',asset_class='EQUITY'))
        await db.commit()
        source=trade(metadata={'employee_id':'IMPERSONATED','department':'Fake','role':'Fake','security':{'currency':'FAKE'},'venue':'SYNTHETIC'})
        saved=await IngestionService().ingest_event(db,source)
        assert saved.metadata['employee_id']=='E1' and saved.metadata['department']=='Research'
        assert saved.metadata['security']['currency']=='USD'
        assert saved.source_metadata==source.metadata
        assert (await IngestionService().ingest_event(db,source)).event_id==saved.event_id
        unresolved=await IngestionService().ingest_event(db,trade(account_id='UNKNOWN',metadata={'employee_id':'E1','department':'Fake','role':'Fake'}))
        assert 'employee_id' not in unresolved.metadata and unresolved.source_metadata['employee_id']=='E1'
        assert await db.scalar(select(func.count()).select_from(EventOutbox))==2


@pytest.mark.asyncio
async def test_source_and_enrichment_remain_snapshot_after_hr_change(platform_db):
    _,sessions,_=platform_db
    async with sessions() as db:
        first={'source_key':'hr-first','employee_id':'E2','full_name':'Synthetic Two','email':'two@sentinel.example',
               'department':'Research','role':'Analyst','status':'ACTIVE','effective_at':'2026-01-01T00:00:00Z',
               'accounts':[{'firm_id':'FIRM','account_id':'ACCOUNT'}]}
        await ingest_hr_feed(db,[first]); await db.commit()
        original=trade()
        stored=await IngestionService().ingest_event(db,original)
        await ingest_hr_feed(db,[{**first,'source_key':'hr-change','department':'Equities','role':'Trader','effective_at':'2026-10-21T00:00:00Z'}])
        await db.commit()
        replay=await IngestionService().ingest_event(db,original)
        assert replay.event_id==stored.event_id
        assert replay.metadata['department']=='Research'
        assert await db.scalar(select(func.count()).select_from(TradingEvent))==1


class PoisonStream:
    async def read_stream(self,*args,**kwargs):
        if args[1]=='1-0': return []
        return [('sentinel:trading_events',[('1-0',{'event_id':'invalid-reference'})])]


@pytest.mark.asyncio
async def test_poison_stream_preserved_retried_then_quarantined(platform_db,monkeypatch):
    _,sessions,_=platform_db
    monkeypatch.setattr(pipeline,'redis_client',PoisonStream())
    notifications=[]
    async def broadcast(data): notifications.append(data)
    for attempt in range(3):
        async with sessions() as db:
            if attempt<2:
                with pytest.raises(ValueError): await pipeline.consume_once(db,broadcast)
            else:
                assert await pipeline.consume_once(db,broadcast)==1
    async with sessions() as db:
        failure=await db.scalar(select(StreamFailure))
        assert failure.attempts==3 and failure.status=='QUARANTINED'
        assert failure.payload=={'event_id':'invalid-reference'}
        assert (await db.get(StreamCursor,'detection-v1')).last_id=='1-0'
        assert await pipeline.consume_once(db,broadcast)==0
    assert notifications==[]


@pytest.mark.asyncio
async def test_real_recovery_requeues_without_deleting_or_duplicating_source(platform_db):
    client,sessions,_=platform_db
    async with sessions() as db:
        saved=await IngestionService().ingest_event(db,trade())
        row=await db.scalar(select(EventOutbox));row.published=True
        db.add(StreamCursor(name='detection-v1',last_id='99-0'));await db.commit()
    for _ in range(2):
        response=await client.post('/api/operations/recover',json={'mode':'REBUILD_STREAM'})
        assert response.status_code==200,response.text
        assert response.json()['events_requeued']==1
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TradingEvent))==1
        assert (await db.get(TradingEvent,saved.event_id)).event_id==saved.event_id
        assert (await db.scalar(select(EventOutbox))).published is False
        assert (await db.get(StreamCursor,'detection-v1')).last_id=='0-0'
        assert await db.scalar(select(func.count()).select_from(PlatformAudit).where(PlatformAudit.target_type=='OPERATION'))==2


@pytest.mark.asyncio
async def test_exact_replay_event_query_isolated_from_other_runs(platform_db):
    client,_,_=platform_db
    runs=[]
    for seed in (911,912):
        response=await client.post('/api/replay/wash_trade',params={'seed':seed})
        assert response.status_code==200,response.text
        runs.append(response.json())
    for run in runs:
        response=await client.get('/api/events',params={'replay_run_id':run['run_id']})
        assert response.status_code==200,response.text
        assert len(response.json())==run['events_generated']
        assert all(row['metadata']['replay_run_id']==run['run_id'] for row in response.json())
    assert (await client.get('/api/events',params={'replay_run_id':'invalid'})).status_code==422
