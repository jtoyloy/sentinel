"""Real persisted communication surveillance and evidence API tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.core.security import get_current_user
from app.domains.communications import Communication, raw_hash, router, simulate_messages
from app.domains.employees import ingest_hr_feed, simulate_hr_records
from app.models.models import Base, TradingEvent
from app.platform import ComplianceSignal


@pytest_asyncio.fixture
async def comms(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/comms.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    async def db_dependency():
        async with maker() as db:
            yield db
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test", username="analyst", role="ANALYST")
    async with maker() as db:
        await ingest_hr_feed(db, simulate_hr_records(42, 2))
        await db.commit()
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield maker, client, app
    await engine.dispose()


def execution(timestamp, *, account="HR-42-0000", firm="SYNTHETIC", symbol="AAPL", metadata=None):
    return TradingEvent(event_id=uuid.uuid4(), event_type="EXECUTION", timestamp=timestamp, instrument_id=symbol, trader_id="unmatched-trader", account_id=account, firm_id=firm, side="BUY", quantity=100, price=Decimal("150.12"), order_id=str(uuid.uuid4()), event_metadata=metadata or {})


@pytest.mark.asyncio
async def test_normal_control_rule_signals_and_durable_idempotency(comms):
    maker, client, _ = comms
    records = simulate_messages()
    response = await client.post("/api/communications/ingest", json={"messages": records})
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 4, "duplicates": 0, "signals_generated": 3}
    assert (await client.post("/api/communications/ingest", json={"messages": records})).json() == {"accepted": 0, "duplicates": 4, "signals_generated": 0}
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(Communication)) == 4
        signals = (await db.scalars(select(ComplianceSignal))).all()
        assert {s.rule for s in signals} == {"INSIDE_INFORMATION", "GUARANTEED_RETURN", "CONCEALMENT"}
        normal = await db.scalar(select(Communication).where(Communication.source_key == records[0]["source_key"]))
        assert all(str(normal.id) not in s.evidence["communications"] for s in signals)
    assert len((await client.get("/api/communications?channel=CHAT")).json()) == 2
    assert (await client.get("/api/communications?limit=0")).status_code == 422


@pytest.mark.asyncio
async def test_correlation_exact_employee_instrument_and_time_boundaries(comms):
    maker, client, _ = comms
    record = simulate_messages()[1]
    at = datetime.fromisoformat(record["timestamp"])
    async with maker() as db:
        matching = execution(at + timedelta(hours=24))
        enriched = execution(at - timedelta(hours=24), account="no-map", metadata={"employee_id": "EMP-42-0000"})
        unrelated = [execution(at, account="HR-42-0001"), execution(at, firm="OTHER"), execution(at + timedelta(hours=24, microseconds=1)), execution(at, symbol="MSFT")]
        db.add_all([matching, enriched] + unrelated)
        await db.commit()
        ids = {str(matching.event_id), str(enriched.event_id)}
    assert (await client.post("/api/communications/ingest", json={"messages": [record]})).json()["signals_generated"] == 1
    row = (await client.get("/api/communications")).json()[0]
    detail = (await client.get(f"/api/communications/{row['id']}")).json()
    assert {e["event_id"] for e in detail["related_events"]} == ids
    signal = detail["signals"][0]
    assert signal["severity"] == "CRITICAL"
    assert set(signal["evidence"]["events"]) == ids
    assert signal["evidence"]["rule_version"] == "1"


@pytest.mark.asyncio
async def test_case_unicode_spans_normalization_and_spoof_sender(comms):
    maker, client, _ = comms
    record = simulate_messages()[1]
    record.update(source_key="unicode", sender="Famous Employee <ATTACKER@outside.example>", body="Résumé 🏦: INSIDE INFORMATION on $aapl before THE ANNOUNCEMENT.\r\n秘密", subject="Synthetic")
    async with maker() as db:
        db.add(execution(datetime.fromisoformat(record["timestamp"])))
        await db.commit()
    assert (await client.post("/api/communications/ingest", json={"messages": [record]})).status_code == 200
    row = (await client.get("/api/communications")).json()[0]
    assert row["sender"] == "attacker@outside.example" and row["employee_id"] is None
    assert row["raw_payload"] == record and "\r\n" not in row["body"]
    detail = (await client.get(f"/api/communications/{row['id']}")).json()
    evidence = detail["signals"][0]["evidence"]
    assert evidence["events"] == [] and evidence["employees"] == []
    assert evidence["entities"][0]["value"] == "AAPL"
    for span in evidence["keyword_spans"]:
        assert row[span["field"]][span["start"]:span["end"]] == span["text"]
    assert (await client.get(f"/api/communications/{row['id']}/evidence")).json()["hash_verified"] is True


@pytest.mark.asyncio
async def test_hash_verifies_raw_and_normalized_evidence_tampering(comms):
    maker, client, _ = comms
    record = simulate_messages()[0]
    await client.post("/api/communications/ingest", json={"messages": [record]})
    row = (await client.get("/api/communications")).json()[0]
    endpoint = f"/api/communications/{row['id']}/evidence"
    evidence = (await client.get(endpoint)).json()
    assert evidence["content_sha256"] == raw_hash(record) and evidence["hash_verified"]
    async with maker() as db:
        stored = await db.get(Communication, uuid.UUID(row["id"]))
        stored.body = "tampered normalized body"
        await db.commit()
    assert not (await client.get(endpoint)).json()["hash_verified"]
    async with maker() as db:
        stored = await db.get(Communication, uuid.UUID(row["id"]))
        stored.body = record["body"]
        stored.raw_payload = {**record, "body": "tampered raw body"}
        await db.commit()
    assert not (await client.get(endpoint)).json()["hash_verified"]


@pytest.mark.asyncio
async def test_conflicting_source_rejected_atomically_and_validation(comms):
    maker, client, _ = comms
    record = simulate_messages()[0]
    await client.post("/api/communications/ingest", json={"messages": [record]})
    changed = {**record, "body": "different message"}
    response = await client.post("/api/communications/ingest", json={"messages": [{**record, "source_key": "new"}, changed]})
    assert response.status_code == 409
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(Communication)) == 1
    invalid = {**record, "source_key": "invalid", "timestamp": "2026-01-01T12:00:00"}
    assert (await client.post("/api/communications/ingest", json={"messages": [invalid]})).status_code == 422
    assert (await client.get(f"/api/communications/{uuid.uuid4()}")).status_code == 404


@pytest.mark.asyncio
async def test_synthetic_determinism_and_role_enforcement(comms):
    _, client, app = comms
    assert simulate_messages(3) == simulate_messages(3)
    assert simulate_messages(3) != simulate_messages(4)
    assert (await client.post("/api/communications/simulate")).json()["accepted"] == 4
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="viewer", username="viewer", role="VIEWER")
    assert (await client.get("/api/communications")).status_code == 200
    assert (await client.post("/api/communications/simulate")).status_code == 403


@pytest.mark.asyncio
async def test_message_first_late_trade_adds_versioned_signal_deduplicates_and_preserves_original(comms):
    from app.domains.communications import correlate_event
    maker, client, _ = comms
    record = simulate_messages()[1]
    assert (await client.post("/api/communications/ingest", json={"messages": [record]})).json()["signals_generated"] == 1
    async with maker() as db:
        original = await db.scalar(select(ComplianceSignal))
        original_id = original.id
        original_evidence = deepcopy(original.evidence)
        assert original_evidence["events"] == []
        event = execution(datetime.fromisoformat(record["timestamp"]) + timedelta(hours=24))
        db.add(event)
        await db.flush()
        signals = await correlate_event(db, event)
        assert len(signals) == 1 and signals[0].rule == "TRADE_CORRELATION"
        assert signals[0].evidence["events"] == [str(event.event_id)]
        assert signals[0].evidence["employees"] == ["EMP-42-0000"]
        assert signals[0].evidence["sender_identity_assurance"] == "UNVERIFIED_FEED"
        assert "INSIDE_INFORMATION" in signals[0].evidence["matched_rules"]
        assert await correlate_event(db, event) == []
        await db.commit()
        event_id = event.event_id
    async with maker() as db:
        assert (await db.get(ComplianceSignal, original_id)).evidence == original_evidence
        event = await db.get(TradingEvent, event_id)
        assert await correlate_event(db, event) == []  # persisted replay deduplication
        assert await db.scalar(select(func.count()).select_from(ComplianceSignal)) == 2


@pytest.mark.asyncio
async def test_late_trade_correlation_rejects_unrelated_identity_ticker_time_and_normal_messages(comms):
    from app.domains.communications import correlate_event
    maker, client, _ = comms
    records = simulate_messages()
    await client.post("/api/communications/ingest", json={"messages": [records[0], records[1]]})
    at = datetime.fromisoformat(records[1]["timestamp"])
    async with maker() as db:
        negatives = [execution(at, account="HR-42-0001"), execution(at, symbol="MSFT"), execution(at + timedelta(hours=24, microseconds=1)), execution(at, metadata={"employee_id": "EMP-42-0001"})]
        order = execution(at)
        order.event_type = "ORDER"
        negatives.append(order)
        db.add_all(negatives)
        await db.flush()
        for event in negatives:
            assert await correlate_event(db, event) == []
        await db.commit()
    # Normal message alone never acquires a correlation signal.
    normal = {**records[0], "source_key": "normal-new-symbol", "body": "Routine liquidity review for $NVDA"}
    await client.post("/api/communications/ingest", json={"messages": [normal]})
    async with maker() as db:
        event = execution(at, symbol="NVDA")
        db.add(event)
        await db.flush()
        assert await correlate_event(db, event) == []


@pytest.mark.asyncio
async def test_late_historical_execution_for_inactive_employee_still_requires_review(comms):
    from app.domains.communications import correlate_event
    from app.domains.employees import Employee
    maker, client, _ = comms
    record = simulate_messages()[1]
    await client.post("/api/communications/ingest", json={"messages": [record]})
    async with maker() as db:
        employee = await db.get(Employee, "EMP-42-0000")
        employee.status = "INACTIVE"
        event = execution(datetime.fromisoformat(record["timestamp"]))
        db.add(event)
        await db.flush()
        signals = await correlate_event(db, event)
        assert len(signals) == 1
        assert signals[0].evidence["employee_status"] == "INACTIVE"
        assert "does not authorize trading" in signals[0].explanation
