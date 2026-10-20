"""Contract and recovery tests using real SQL persistence and detector logic.

Only Redis transport is replaced. Every request gets its own database session;
ASGITransport deliberately does not start the production dependency lifespan.
"""
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app
from app.models.models import Alert, EventOutbox, ReplayRun, StreamCursor, TradingEvent
from app.services import pipeline

BASE_TIME = "2026-10-20T14:30:00Z"


class MemoryStream:
    """Minimal Redis Streams semantics, including strict cursor reads."""

    def __init__(self):
        self.messages = []
        self.fail_after = None
        self.read_cursors = []

    async def publish_event(self, stream_name, payload):
        if self.fail_after is not None and len(self.messages) >= self.fail_after:
            raise ConnectionError("Redis unavailable")
        stream_id = f"{len(self.messages) + 1}-0"
        self.messages.append((stream_id, dict(payload)))
        return stream_id

    async def read_stream(self, stream_name, last_id, count=100):
        self.read_cursors.append(last_id)
        sequence = int(last_id.split("-")[0])
        messages = [item for item in self.messages if int(item[0].split("-")[0]) > sequence]
        return [(stream_name, messages[:count])] if messages else []


@pytest_asyncio.fixture
async def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = session_dependency
    stream = MemoryStream()
    monkeypatch.setattr(pipeline, "redis_client", stream)
    notifications = []

    async def broadcast(message):
        notifications.append(message)

    async def drain():
        async with sessions() as session:
            await pipeline.publish_outbox(session)
            return await pipeline.consume_once(session, broadcast)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions, stream, notifications, drain
    app.dependency_overrides.clear()
    await engine.dispose()


def event(**overrides):
    return {
        "event_id": str(uuid4()), "event_type": "EXECUTION", "timestamp": BASE_TIME,
        "instrument_id": "UNLISTED", "trader_id": "TRADER", "account_id": "ACCOUNT",
        "firm_id": "FIRM", "side": "BUY", "quantity": 500, "price": "101.125",
        "order_id": "ORDER", "metadata": {"beneficial_owner": "CLIENT", "venue": "NYSE"},
        **overrides,
    }


async def replay(client, scenario="wash_trade"):
    response = await client.post(f"/api/replay/{scenario}", params={"seed": 19, "base_time": BASE_TIME})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_replay_determinism_persistence_and_isolated_runs(harness):
    client, sessions, _, _, drain = harness
    first, second = await replay(client), await replay(client)
    assert first["run_id"] != second["run_id"]
    assert first["scenario"] == second["scenario"] == "wash_trade"
    assert first["seed"] == second["seed"] == 19
    assert datetime.fromisoformat(first["base_time"].replace("Z", "+00:00")) == datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc)
    async with sessions() as session:
        runs = (await session.scalars(select(ReplayRun))).all()
        rows = (await session.scalars(select(TradingEvent).order_by(TradingEvent.timestamp))).all()
        assert {str(run.run_id) for run in runs} == {first["run_id"], second["run_id"]}
        assert all(run.seed == 19 and run.event_count == first["events_generated"] for run in runs)
        groups = [[row for row in rows if row.event_metadata["replay_run_id"] == run["run_id"]] for run in (first, second)]
        assert len(groups[0]) == len(groups[1]) == first["events_generated"]
        for field in ("order_id", "trader_id", "account_id"):
            assert {getattr(row, field) for row in groups[0]}.isdisjoint({getattr(row, field) for row in groups[1]})
        assert {row.event_metadata["beneficial_owner"] for row in groups[0]}.isdisjoint({row.event_metadata["beneficial_owner"] for row in groups[1]})
        def market_content(group):
            return [(row.event_type, row.timestamp, row.instrument_id, row.side, row.quantity, row.price) for row in group]
        assert market_content(groups[0]) == market_content(groups[1])
    await drain()
    alerts = (await client.get("/api/alerts")).json()
    assert len(alerts) == 2  # One wash pair per run, never cross-run matches.


@pytest.mark.asyncio
async def test_invalid_replay_does_not_write(harness):
    client, sessions, _, _, _ = harness
    for scenario, params in (("not-a-scenario", {}), ("wash_trade", {"base_time": "2026-10-20T14:30:00"})):
        response = await client.post(f"/api/replay/{scenario}", params=params)
        assert response.status_code == 422, response.text
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TradingEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(ReplayRun)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"timestamp": "2026-10-20T14:30:00"}, {"quantity": 0}, {"price": "NaN"},
    {"price": "Infinity"}, {"price": "0"}, {"side": "HOLD"},
    {"account_id": " "}, {"order_id": ""}, {"instrument_id": "x" * 51},
])
async def test_event_validation_rejects_invalid_content(harness, changes):
    client, sessions, _, _, _ = harness
    response = await client.post("/api/events/ingest", json=event(**changes))
    assert response.status_code == 422, response.text
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TradingEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(EventOutbox)) == 0


@pytest.mark.asyncio
async def test_idempotency_conflict_and_atomic_batch(harness):
    client, sessions, _, _, _ = harness
    original = event()
    first = await client.post("/api/events/ingest", json=original)
    assert first.status_code == 200, first.text
    repeated = await client.post("/api/events/ingest", json=original)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["event_id"] == first.json()["event_id"]
    conflict = await client.post("/api/events/ingest", json={**original, "quantity": 501})
    assert conflict.status_code == 409, conflict.text
    # A conflict at the end must roll back earlier rows in the same batch.
    batch = await client.post("/api/events/ingest/batch", json=[event(order_id="NEW"), {**original, "price": "102"}])
    assert batch.status_code == 409, batch.text
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TradingEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(EventOutbox)) == 1
    rows = (await client.get("/api/events")).json()
    assert rows[0]["metadata"] == original["metadata"]
    assert rows[0]["quantity"] == 500


@pytest.mark.asyncio
async def test_evidence_audit_stats_filters_and_pagination(harness):
    client, sessions, _, notifications, drain = harness
    for scenario in ("wash_trade", "spoofing"):
        await replay(client, scenario)
    # A nearby unrelated event must never appear in an alert's evidence.
    unrelated = event(order_id="UNRELATED", instrument_id="AAPL")
    assert (await client.post("/api/events/ingest", json=unrelated)).status_code == 200
    await drain()
    all_alerts_response = await client.get("/api/alerts")
    assert all_alerts_response.status_code == 200
    all_alerts = all_alerts_response.json()
    assert {alert["alert_type"] for alert in all_alerts} == {"WASH_TRADE", "SPOOFING"}
    assert {message["alert_id"] for message in notifications} == {alert["alert_id"] for alert in all_alerts}
    for alert in all_alerts:
        assert alert["explanation"] and alert["description"]
        detail = await client.get(f"/api/alerts/{alert['alert_id']}")
        assert detail.json() == alert
        evidence = (await client.get(f"/api/alerts/{alert['alert_id']}/evidence")).json()
        assert {row["event_id"] for row in evidence} == set(alert["evidence"]["event_ids"])
        assert unrelated["event_id"] not in {row["event_id"] for row in evidence}
        evidence_times = [datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in evidence]
        assert evidence_times == sorted(evidence_times)
        assert all(row["metadata"]["source"] == "synthetic" for row in evidence)
        if alert["alert_type"] == "SPOOFING":
            assert {row["event_type"] for row in evidence} == {"ORDER", "CANCEL", "EXECUTION"}
    for offset, alert in enumerate(all_alerts):
        assert (await client.get("/api/alerts", params={"limit": 1, "offset": offset})).json() == [alert]
    wash = next(alert for alert in all_alerts if alert["alert_type"] == "WASH_TRADE")
    assert (await client.get("/api/alerts", params={"alert_type": "WASH_TRADE", "instrument_id": wash["instrument_id"]})).json() == [wash]
    alert_id = wash["alert_id"]
    for status, previous in (("INVESTIGATING", "OPEN"), ("RESOLVED", "INVESTIGATING"), ("OPEN", "RESOLVED")):
        response = await client.patch(f"/api/alerts/{alert_id}", json={"status": status, "actor": "analyst@example.com", "notes": f"Move from {previous} to {status}"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status
        assert bool(response.json()["resolved_at"]) == (status == "RESOLVED")
        filtered = (await client.get("/api/alerts", params={"status": status})).json()
        assert any(row["alert_id"] == alert_id for row in filtered)
        assert all(row["status"] == status for row in filtered)
        if status == "RESOLVED":
            assert (await client.get("/api/stats")).json()["open_alerts"] == len(all_alerts) - 1
    history = (await client.get(f"/api/alerts/{alert_id}/history")).json()
    assert [(row["previous_status"], row["new_status"]) for row in history] == [("OPEN", "INVESTIGATING"), ("INVESTIGATING", "RESOLVED"), ("RESOLVED", "OPEN")]
    assert all(row["actor"] == "analyst@example.com" and row["notes"] for row in history)
    invalid = await client.patch(f"/api/alerts/{alert_id}", json={"status": "DELETED", "actor": "analyst", "notes": "invalid"})
    assert invalid.status_code == 422
    assert len((await client.get(f"/api/alerts/{alert_id}/history")).json()) == 3
    for suffix in ("", "/history", "/evidence"):
        assert (await client.get(f"/api/alerts/{uuid4()}{suffix}")).status_code == 404
    stats = (await client.get("/api/stats")).json()
    async with sessions() as session:
        expected_events = await session.scalar(select(func.count()).select_from(TradingEvent))
    assert stats == {"events": expected_events, "alerts": len(all_alerts), "open_alerts": len(all_alerts), "critical_alerts": sum(alert["severity"] == "CRITICAL" for alert in all_alerts)}
    events = (await client.get("/api/events", params={"limit": 1000})).json()
    assert (await client.get("/api/events", params={"limit": 2, "offset": 1})).json() == events[1:3]
    assert (await client.get("/api/events", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/alerts", params={"offset": -1})).status_code == 422


@pytest.mark.asyncio
async def test_outbox_partial_failure_restart_cursor_and_alert_dedup(harness):
    client, sessions, stream, notifications, drain = harness
    run = await replay(client)
    stream.fail_after = 1
    async with sessions() as session:
        with pytest.raises(ConnectionError, match="Redis unavailable"):
            await pipeline.publish_outbox(session)
        await session.rollback()
    async with sessions() as session:
        rows = (await session.scalars(select(EventOutbox))).all()
        assert len(rows) == run["events_generated"]
        assert all(not row.published for row in rows)
    # The first stream message survived while its DB transaction rolled back.
    stream.fail_after = None
    count = await drain()
    assert count == run["events_generated"] + 1
    assert len(notifications) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Alert)) == 1
        cursor = await session.get(StreamCursor, "detection-v1")
        assert cursor.last_id == stream.messages[-1][0]
        assert all(row.published for row in (await session.scalars(select(EventOutbox))).all())
    # A fresh worker session resumes after the saved cursor without rebroadcast.
    assert await drain() == 0
    assert stream.read_cursors[-1] == stream.messages[-1][0]
    assert len(notifications) == 1
    # Simulate redelivery beyond the saved cursor: detection remains deduplicated.
    await stream.publish_event(pipeline.STREAM, stream.messages[0][1])
    assert await drain() == 1
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_normal_replay_persists_without_suspicious_alerts(harness):
    client, sessions, _, notifications, drain = harness
    run = await replay(client, "normal")
    assert run["events_generated"] == 100
    assert await drain() == 100
    assert (await client.get("/api/alerts")).json() == []
    assert notifications == []
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TradingEvent)) == 100
        assert await session.scalar(select(func.count()).select_from(Alert)) == 0


@pytest.mark.asyncio
async def test_spoof_snapshot_survives_delayed_evidence_without_duplicate_alert(harness):
    """Late messages change live context without multiplying the original case."""
    from datetime import timedelta
    from app.services.detection import DetectionEngine

    client, sessions, _, notifications, drain = harness
    await replay(client, "spoofing")
    await drain()
    original_alerts = (await client.get("/api/alerts")).json()
    assert len(original_alerts) == 1
    snapshot = original_alerts[0]
    assert snapshot["alert_type"] == "SPOOFING"
    assert "Observation snapshot" in snapshot["explanation"]
    evidence = (await client.get(f"/api/alerts/{snapshot['alert_id']}/evidence")).json()
    large_order = next(row for row in evidence if row["event_type"] == "ORDER")
    order_time = datetime.fromisoformat(large_order["timestamp"].replace("Z", "+00:00"))

    # Delivered later, but executed during the resting interval. A fingerprint
    # over every observed evidence ID would incorrectly open a second case.
    delayed_trade = {key: value for key, value in large_order.items() if key not in ("event_id", "created_at")}
    delayed_trade.update(event_id=str(uuid4()), event_type="EXECUTION", order_id="DELAYED_OPPOSITE",
                         side="BUY" if large_order["side"] == "SELL" else "SELL", quantity=100,
                         timestamp=(order_time + timedelta(seconds=2.5)).isoformat())
    response = await client.post("/api/events/ingest", json=delayed_trade)
    assert response.status_code == 200, response.text
    await drain()
    assert (await client.get("/api/alerts")).json() == [snapshot]
    assert len(notifications) == 1
    async with sessions() as session:
        detections = await DetectionEngine().detect_spoofing(
            session, large_order["instrument_id"], order_time, order_time + timedelta(seconds=15))
        assert len(detections) == 1
        assert delayed_trade["event_id"] in {str(row.event_id) for row in detections[0]["opposite_trades"]}

    # A delayed partial fill of the large order invalidates the detector's
    # current no-execution premise; the historical observation remains explicit.
    late_fill = {key: value for key, value in large_order.items() if key not in ("event_id", "created_at")}
    late_fill.update(event_id=str(uuid4()), event_type="EXECUTION", quantity=50,
                     timestamp=(order_time + timedelta(seconds=3)).isoformat())
    response = await client.post("/api/events/ingest", json=late_fill)
    assert response.status_code == 200, response.text
    await drain()
    assert (await client.get("/api/alerts")).json() == [snapshot]
    assert len(notifications) == 1
    async with sessions() as session:
        assert await DetectionEngine().detect_spoofing(
            session, large_order["instrument_id"], order_time, order_time + timedelta(seconds=15)) == []
        assert await session.scalar(select(func.count()).select_from(Alert)) == 1
    persisted_evidence = (await client.get(f"/api/alerts/{snapshot['alert_id']}/evidence")).json()
    assert {row["event_id"] for row in persisted_evidence} == set(snapshot["evidence"]["event_ids"])
