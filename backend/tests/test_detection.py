"""Rule boundary tests use persisted events and real SQL queries."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.database import Base
from app.models.models import TradingEvent
from app.models.schemas import TradingEventCreate
from app.services.detection import DetectionEngine
from app.services.generator import SyntheticDataGenerator

BASE_TIME = datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def detector_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def event(kind="EXECUTION", seconds=0, side="BUY", **kwargs):
    values = dict(event_id=uuid.uuid4(), event_type=kind, timestamp=BASE_TIME + timedelta(seconds=seconds),
                  instrument_id="AAPL", trader_id="T1", account_id="A1", firm_id="F1", side=side,
                  quantity=1000, price=Decimal("100"), order_id=f"ORDER_{uuid.uuid4()}", event_metadata={})
    values.update(kwargs)
    return TradingEvent(**values)


async def detect(session, events, kind="wash"):
    session.add_all(events)
    await session.commit()
    detector = DetectionEngine()
    method = detector.detect_wash_trades if kind == "wash" else detector.detect_spoofing
    return await method(session, "AAPL", BASE_TIME - timedelta(seconds=60), BASE_TIME + timedelta(seconds=90))


@pytest.mark.asyncio
async def test_wash_sell_before_buy_and_common_owner_across_accounts(detector_db):
    sell = event(seconds=0, side="SELL", account_id="A2", trader_id="T2", event_metadata={"beneficial_owner": "OWNER"})
    buy = event(seconds=2, event_metadata={"beneficial_owner": "OWNER"})
    detections = await detect(detector_db, [buy, sell])
    assert len(detections) == 1
    assert detections[0]["buy_event"].event_id == buy.event_id
    assert detections[0]["sell_event"].event_id == sell.event_id
    assert detections[0]["ownership_basis"] == "beneficial_owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"account_id": "UNRELATED"},
    {"firm_id": "OTHER_FIRM"},
    {"event_metadata": {"beneficial_owner": "KNOWN_ONLY_ON_ONE_SIDE"}},
    {"seconds": 30.001},
    {"price": Decimal("101.01")},
    {"quantity": 1200},
    {"side": "BUY"},
    {"instrument_id": "MSFT"},
])
async def test_wash_does_not_flag_unrelated_or_outside_thresholds(detector_db, changes):
    values = {"seconds": 2, "side": "SELL", **changes}
    assert await detect(detector_db, [event(), event(**values)]) == []


@pytest.mark.asyncio
async def test_conflicting_explicit_owners_override_shared_account(detector_db):
    assert await detect(detector_db, [
        event(event_metadata={"beneficial_owner": "OWNER_A"}),
        event(seconds=1, side="SELL", event_metadata={"beneficial_owner": "OWNER_B"}),
    ]) == []


@pytest.mark.asyncio
async def test_wash_inclusive_time_price_quantity_thresholds(detector_db):
    detections = await detect(detector_db, [event(quantity=900), event(seconds=30, side="SELL", price=Decimal("101"))])
    assert len(detections) == 1
    assert detections[0]["qty_diff"] == 100


def spoof_events(cancel_seconds=4, trade_seconds=2, **cancel_changes):
    order = event("ORDER", quantity=50000, side="SELL", order_id="LARGE")
    trade = event(seconds=trade_seconds, side="BUY", trader_id="SAME_ACCOUNT_OTHER_TRADER")
    values = {"parent_order_id": "LARGE", "quantity": 50000, **cancel_changes}
    cancel = event("CANCEL", seconds=cancel_seconds, side="SELL", **values)
    return order, trade, cancel


@pytest.mark.asyncio
async def test_spoof_requires_opposite_execution_while_resting_and_cancel_evidence(detector_db):
    order, trade, cancel = spoof_events(cancel_seconds=15)
    detections = await detect(detector_db, [cancel, trade, order], "spoof")
    assert len(detections) == 1
    assert detections[0]["cancel_event"].event_id == cancel.event_id
    assert [item.event_id for item in detections[0]["opposite_trades"]] == [trade.event_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"cancel_seconds": 15.001}, {"cancel_seconds": -1},
    {"trade_seconds": -0.001}, {"trade_seconds": 4.001},
    {"account_id": "OTHER_ACCOUNT"}, {"firm_id": "OTHER_FIRM"},
    {"parent_order_id": "OTHER_ORDER"},
])
async def test_spoof_rejects_bad_timing_or_cancel_identity(detector_db, changes):
    assert await detect(detector_db, spoof_events(**changes), "spoof") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["order_id", "parent_order_id"])
async def test_spoof_rejects_partial_execution_of_large_order(detector_db, reference):
    events = list(spoof_events())
    events.append(event(seconds=1, side="SELL", quantity=1, **{reference: "LARGE"}))
    assert await detect(detector_db, events, "spoof") == []


@pytest.mark.asyncio
async def test_spoof_rejects_unrelated_opposite_execution(detector_db):
    order, trade, cancel = spoof_events()
    trade.account_id = "UNRELATED"
    assert await detect(detector_db, [order, trade, cancel], "spoof") == []


@pytest.mark.parametrize("scenario", ["wash_trade", "spoofing", "normal", "mixed"])
def test_seeded_replay_is_deterministic_and_schema_valid(scenario):
    first = SyntheticDataGenerator(BASE_TIME, seed=42).generate_scenario(scenario)
    second = SyntheticDataGenerator(BASE_TIME, seed=42).generate_scenario(scenario)
    third = SyntheticDataGenerator(BASE_TIME, seed=43).generate_scenario(scenario)
    assert first == second
    assert first != third
    assert first == sorted(first, key=lambda item: item["timestamp"])
    for item in first:
        TradingEventCreate(**item)
    orders = {item["order_id"]: item for item in first if item["event_type"] == "ORDER"}
    for item in first:
        if item["event_type"] in {"EXECUTION", "CANCEL"}:
            assert item["order_id"] in orders
            assert item["timestamp"] >= orders[item["order_id"]]["timestamp"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,expected", [("wash_trade", {"WASH_TRADE"}), ("spoofing", {"SPOOFING"}),
                                                ("mixed", {"WASH_TRADE", "SPOOFING"}), ("normal", set())])
async def test_generated_scenarios_detect_expected_rules(detector_db, scenario, expected):
    payloads = SyntheticDataGenerator(BASE_TIME, seed=42).generate_scenario(scenario)
    for payload in payloads:
        values = dict(payload)
        values["event_metadata"] = values.pop("metadata")
        values["timestamp"] = datetime.fromisoformat(values["timestamp"])
        values["event_id"] = uuid.uuid4()
        detector_db.add(TradingEvent(**values))
    await detector_db.commit()
    end = max(datetime.fromisoformat(item["timestamp"]) for item in payloads)
    detections = []
    for instrument in {item["instrument_id"] for item in payloads}:
        detections.extend(await DetectionEngine().process_detection_window(detector_db, instrument, window_minutes=60, end_time=end))
    assert {item["type"] for item in detections} == expected
    assert len(detections) == len(expected)


def test_generator_rejects_unknown_scenarios_and_naive_time():
    with pytest.raises(ValueError):
        SyntheticDataGenerator(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        SyntheticDataGenerator(BASE_TIME).generate_scenario("made_up")
