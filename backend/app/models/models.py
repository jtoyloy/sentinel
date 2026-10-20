from sqlalchemy import Column, String, Integer, Numeric, TIMESTAMP, CheckConstraint, Index, Text, JSON, Uuid, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
import uuid
from datetime import datetime, timezone
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = TIMESTAMP(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class TradingEvent(Base):
    __tablename__ = "trading_events"

    event_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(20), nullable=False)
    timestamp = Column(UTCDateTime(), nullable=False)
    instrument_id = Column(String(50), nullable=False)
    trader_id = Column(String(50), nullable=False)
    account_id = Column(String(50), nullable=False)
    firm_id = Column(String(50), nullable=False)
    side = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    order_id = Column(String(100), nullable=False)
    parent_order_id = Column(String(100), nullable=True)
    source_metadata = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    event_metadata = Column(JSON().with_variant(JSONB, "postgresql"), default=dict)
    created_at = Column(UTCDateTime(), default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("event_type IN ('ORDER', 'EXECUTION', 'CANCEL', 'MODIFY')", name="check_event_type"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="check_side"),
        CheckConstraint("quantity > 0", name="check_quantity_positive"),
        CheckConstraint("price > 0", name="check_price_positive"),
        Index("idx_trading_events_timestamp_instrument", "timestamp", "instrument_id"),
        Index("idx_trading_events_trader", "trader_id", "timestamp"),
        Index("idx_trading_events_order", "order_id"),
        Index("idx_trading_events_instrument_time", "instrument_id", "timestamp"),
        Index("idx_trading_events_account", "account_id", "timestamp"),
        Index("idx_trading_events_type_time", "event_type", "timestamp"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    alert_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint = Column(String(64), nullable=False, unique=True)
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="OPEN")
    created_at = Column(UTCDateTime(), nullable=False, default=datetime.utcnow)
    instrument_id = Column(String(50), nullable=False)
    time_window_start = Column(UTCDateTime(), nullable=False)
    time_window_end = Column(UTCDateTime(), nullable=False)
    description = Column(Text, nullable=False)
    explanation = Column(Text, nullable=False)
    evidence = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    confidence_score = Column(Numeric(5, 4), nullable=True)
    assigned_to = Column(String(100), nullable=True)
    resolved_at = Column(UTCDateTime(), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    alert_metadata = Column(JSON().with_variant(JSONB, "postgresql"), default=dict)

    __table_args__ = (
        CheckConstraint("alert_type IN ('WASH_TRADE', 'SPOOFING', 'LAYERING', 'MARKING_CLOSE')", name="check_alert_type"),
        CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name="check_severity"),
        CheckConstraint("status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'FALSE_POSITIVE')", name="check_status"),
        CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name="check_confidence_range"),
        Index("idx_alerts_status_created", "status", "created_at"),
        Index("idx_alerts_type_instrument", "alert_type", "instrument_id", "created_at"),
        Index("idx_alerts_created", "created_at"),
        Index("idx_alerts_instrument", "instrument_id", "created_at"),
        Index("idx_alerts_severity", "severity", "created_at"),
        Index("idx_alerts_time_window", "time_window_start", "time_window_end"),
    )


class DetectionState(Base):
    __tablename__ = "detection_state"

    detector_id = Column(String(50), primary_key=True)
    last_processed_timestamp = Column(UTCDateTime(), nullable=False)
    last_processed_event_id = Column(Uuid(as_uuid=True), nullable=True)
    state_data = Column(JSON().with_variant(JSONB, "postgresql"), default=dict)
    updated_at = Column(UTCDateTime(), nullable=False, default=datetime.utcnow)


class EventOutbox(Base):
    __tablename__ = "event_outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Uuid(as_uuid=True), ForeignKey("trading_events.event_id"), nullable=False, unique=True)
    published = Column(Boolean, nullable=False, default=False, index=True)


class StreamCursor(Base):
    __tablename__ = "stream_cursor"
    name = Column(String(50), primary_key=True)
    last_id = Column(String(50), nullable=False, default="0-0")


class AlertAudit(Base):
    __tablename__ = "alert_audit_log"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id = Column(Uuid(as_uuid=True), ForeignKey("alerts.alert_id"), nullable=False, index=True)
    previous_status = Column(String(20), nullable=False)
    new_status = Column(String(20), nullable=False)
    actor = Column(String(100), nullable=False)
    notes = Column(Text, nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=datetime.utcnow)


class ReplayRun(Base):
    __tablename__ = "replay_runs"
    run_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario = Column(String(20), nullable=False)
    seed = Column(Integer, nullable=False)
    base_time = Column(UTCDateTime(), nullable=False)
    event_count = Column(Integer, nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=datetime.utcnow)
