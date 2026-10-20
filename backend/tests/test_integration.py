import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.models.models import Base, TradingEvent, Alert
from app.services.ingestion import IngestionService
from app.services.detection import DetectionEngine
from app.services.alerting import AlertingService
from app.services.generator import SyntheticDataGenerator
from app.models.schemas import TradingEventCreate

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def db_session():
    """Create a test database session."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session = async_session_maker()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()

@pytest.mark.asyncio
async def test_ingest_event(db_session):
    """Test ingesting a single trading event."""
    ingestion_service = IngestionService()

    event_data = TradingEventCreate(
        event_type="ORDER",
        timestamp=datetime.now(timezone.utc),
        instrument_id="AAPL",
        trader_id="T001",
        account_id="ACC001",
        firm_id="FIRM_A",
        side="BUY",
        quantity=1000,
        price=150.25,
        order_id="ORD_001",
        metadata={}
    )

    result = await ingestion_service.ingest_event(db_session, event_data)

    assert result.event_id is not None
    assert result.instrument_id == "AAPL"
    assert result.trader_id == "T001"
    assert result.quantity == 1000

@pytest.mark.asyncio
async def test_wash_trade_detection(db_session):
    """Test wash trade detection algorithm."""
    ingestion_service = IngestionService()
    detection_engine = DetectionEngine()
    generator = SyntheticDataGenerator()

    # Generate wash trade scenario
    events_data = generator.generate_wash_trade()

    # Ingest events
    for event_dict in events_data:
        event = TradingEventCreate(**event_dict)
        await ingestion_service.ingest_event(db_session, event)

    # Run detection
    start_time = datetime.fromisoformat(events_data[0]["timestamp"])
    end_time = datetime.fromisoformat(events_data[-1]["timestamp"])
    instrument = events_data[0]["instrument_id"]

    detections = await detection_engine.detect_wash_trades(
        db_session, instrument, start_time, end_time
    )

    # Should detect at least one wash trade
    assert len(detections) > 0
    assert detections[0]["confidence"] > 0.5

@pytest.mark.asyncio
async def test_spoofing_detection(db_session):
    """Test spoofing detection algorithm."""
    ingestion_service = IngestionService()
    detection_engine = DetectionEngine()
    generator = SyntheticDataGenerator()

    # Generate spoofing scenario
    events_data = generator.generate_spoofing()

    # Ingest events
    for event_dict in events_data:
        event = TradingEventCreate(**event_dict)
        await ingestion_service.ingest_event(db_session, event)

    # Run detection
    start_time = datetime.fromisoformat(events_data[0]["timestamp"])
    end_time = datetime.fromisoformat(events_data[-1]["timestamp"])
    instrument = events_data[0]["instrument_id"]

    detections = await detection_engine.detect_spoofing(
        db_session, instrument, start_time, end_time
    )

    # Should detect at least one spoofing pattern
    assert len(detections) > 0
    assert detections[0]["confidence"] > 0.4

@pytest.mark.asyncio
async def test_alert_creation(db_session):
    """Test alert creation from detection."""
    ingestion_service = IngestionService()
    detection_engine = DetectionEngine()
    alerting_service = AlertingService()
    generator = SyntheticDataGenerator()

    # Generate and ingest wash trade
    events_data = generator.generate_wash_trade()
    for event_dict in events_data:
        event = TradingEventCreate(**event_dict)
        await ingestion_service.ingest_event(db_session, event)

    # Run detection
    start_time = datetime.fromisoformat(events_data[0]["timestamp"])
    end_time = datetime.fromisoformat(events_data[-1]["timestamp"])
    instrument = events_data[0]["instrument_id"]

    detections = await detection_engine.detect_wash_trades(
        db_session, instrument, start_time, end_time
    )

    # Create alert from detection
    detection_with_meta = {
        "type": "WASH_TRADE",
        "detection": detections[0],
        "window_start": start_time,
        "window_end": end_time
    }

    alert = await alerting_service.create_alert_from_detection(
        db_session, detection_with_meta
    )

    assert alert.alert_id is not None
    assert alert.alert_type == "WASH_TRADE"
    assert alert.severity in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert alert.status == "OPEN"
    assert len(alert.evidence["event_ids"]) >= 2

@pytest.mark.asyncio
async def test_end_to_end_pipeline(db_session):
    """Test complete pipeline: ingest -> detect -> alert."""
    ingestion_service = IngestionService()
    detection_engine = DetectionEngine()
    alerting_service = AlertingService()
    generator = SyntheticDataGenerator()

    # Generate mixed scenario (normal + suspicious)
    events_data = generator.generate_scenario("mixed")

    # Ingest all events
    for event_dict in events_data:
        event = TradingEventCreate(**event_dict)
        await ingestion_service.ingest_event(db_session, event)

    # Get unique instruments
    instruments = list(set(e["instrument_id"] for e in events_data))

    all_alerts = []

    # Run detection for each instrument
    for instrument in instruments:
        start_time = datetime.fromisoformat(min(e["timestamp"] for e in events_data))
        end_time = datetime.fromisoformat(max(e["timestamp"] for e in events_data))

        detections = await detection_engine.process_detection_window(
            db_session, instrument, window_minutes=60, end_time=end_time
        )

        # Create alerts
        for detection in detections:
            alert = await alerting_service.create_alert_from_detection(
                db_session, detection
            )
            all_alerts.append(alert)

    # Should have detected some suspicious patterns
    assert len(all_alerts) > 0

    # Verify we can query alerts
    queried_alerts = await alerting_service.get_alerts(db_session, limit=100)
    assert len(queried_alerts) == len(all_alerts)

@pytest.mark.asyncio
async def test_synthetic_generator_scenarios(db_session):
    """Test all synthetic data generator scenarios."""
    generator = SyntheticDataGenerator()

    # Test wash trade generation
    wash_events = generator.generate_wash_trade()
    assert len(wash_events) > 0
    assert any(e["event_type"] == "EXECUTION" for e in wash_events)

    # Test spoofing generation
    spoof_events = generator.generate_spoofing()
    assert len(spoof_events) > 0
    assert any(e["event_type"] == "CANCEL" for e in spoof_events)

    # Test normal trading
    normal_events = generator.generate_normal_trading(count=50)
    assert len(normal_events) > 0

    # Test mixed scenario
    mixed_events = generator.generate_scenario("mixed")
    assert len(mixed_events) > 50
