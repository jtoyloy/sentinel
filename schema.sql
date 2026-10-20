-- Frozen PostgreSQL schema; identical DDL to Alembic revision 0001.
-- Empty-database initialization only. Refuses legacy tables without changing data.
-- Prefer `alembic upgrade head` for tracked schema management.
-- Running this file directly does not stamp Alembic revision state.
BEGIN;

DO $$
DECLARE existing_tables text;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name) INTO existing_tables
    FROM information_schema.tables
    WHERE table_schema = current_schema()
      AND table_name IN ('alerts', 'detection_state', 'replay_runs', 'stream_cursor', 'trading_events', 'alert_audit_log', 'event_outbox');
    IF existing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'Sentinel initial migration requires an empty application schema; existing tables: %. Back up this database and use an explicit legacy migration; no existing data was changed.', existing_tables;
    END IF;
END;
$$;

CREATE TABLE alerts (
	alert_id UUID NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	alert_type VARCHAR(50) NOT NULL,
	severity VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	instrument_id VARCHAR(50) NOT NULL,
	time_window_start TIMESTAMP WITH TIME ZONE NOT NULL,
	time_window_end TIMESTAMP WITH TIME ZONE NOT NULL,
	description TEXT NOT NULL,
	explanation TEXT NOT NULL,
	evidence JSONB NOT NULL,
	confidence_score NUMERIC(5, 4),
	assigned_to VARCHAR(100),
	resolved_at TIMESTAMP WITH TIME ZONE,
	resolution_notes TEXT,
	alert_metadata JSONB,
	PRIMARY KEY (alert_id),
	CONSTRAINT check_alert_type CHECK (alert_type IN ('WASH_TRADE', 'SPOOFING', 'LAYERING', 'MARKING_CLOSE')),
	CONSTRAINT check_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
	CONSTRAINT check_status CHECK (status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'FALSE_POSITIVE')),
	CONSTRAINT check_confidence_range CHECK (confidence_score >= 0 AND confidence_score <= 1),
	UNIQUE (fingerprint)
);

CREATE INDEX idx_alerts_created ON alerts (created_at);

CREATE INDEX idx_alerts_instrument ON alerts (instrument_id, created_at);

CREATE INDEX idx_alerts_severity ON alerts (severity, created_at);

CREATE INDEX idx_alerts_status_created ON alerts (status, created_at);

CREATE INDEX idx_alerts_time_window ON alerts (time_window_start, time_window_end);

CREATE INDEX idx_alerts_type_instrument ON alerts (alert_type, instrument_id, created_at);

CREATE TABLE detection_state (
	detector_id VARCHAR(50) NOT NULL,
	last_processed_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
	last_processed_event_id UUID,
	state_data JSONB,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (detector_id)
);

CREATE TABLE replay_runs (
	run_id UUID NOT NULL,
	scenario VARCHAR(20) NOT NULL,
	seed INTEGER NOT NULL,
	base_time TIMESTAMP WITH TIME ZONE NOT NULL,
	event_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (run_id)
);

CREATE TABLE stream_cursor (
	name VARCHAR(50) NOT NULL,
	last_id VARCHAR(50) NOT NULL,
	PRIMARY KEY (name)
);

CREATE TABLE trading_events (
	event_id UUID NOT NULL,
	event_type VARCHAR(20) NOT NULL,
	timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
	instrument_id VARCHAR(50) NOT NULL,
	trader_id VARCHAR(50) NOT NULL,
	account_id VARCHAR(50) NOT NULL,
	firm_id VARCHAR(50) NOT NULL,
	side VARCHAR(10) NOT NULL,
	quantity INTEGER NOT NULL,
	price NUMERIC(20, 8) NOT NULL,
	order_id VARCHAR(100) NOT NULL,
	parent_order_id VARCHAR(100),
	event_metadata JSONB,
	created_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (event_id),
	CONSTRAINT check_event_type CHECK (event_type IN ('ORDER', 'EXECUTION', 'CANCEL', 'MODIFY')),
	CONSTRAINT check_side CHECK (side IN ('BUY', 'SELL')),
	CONSTRAINT check_quantity_positive CHECK (quantity > 0),
	CONSTRAINT check_price_positive CHECK (price > 0)
);

CREATE INDEX idx_trading_events_account ON trading_events (account_id, timestamp);

CREATE INDEX idx_trading_events_instrument_time ON trading_events (instrument_id, timestamp);

CREATE INDEX idx_trading_events_order ON trading_events (order_id);

CREATE INDEX idx_trading_events_timestamp_instrument ON trading_events (timestamp, instrument_id);

CREATE INDEX idx_trading_events_trader ON trading_events (trader_id, timestamp);

CREATE INDEX idx_trading_events_type_time ON trading_events (event_type, timestamp);

CREATE TABLE alert_audit_log (
	id UUID NOT NULL,
	alert_id UUID NOT NULL,
	previous_status VARCHAR(20) NOT NULL,
	new_status VARCHAR(20) NOT NULL,
	actor VARCHAR(100) NOT NULL,
	notes TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(alert_id) REFERENCES alerts (alert_id)
);

CREATE INDEX ix_alert_audit_log_alert_id ON alert_audit_log (alert_id);

CREATE TABLE event_outbox (
	id SERIAL NOT NULL,
	event_id UUID NOT NULL,
	published BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (event_id),
	FOREIGN KEY(event_id) REFERENCES trading_events (event_id)
);

CREATE INDEX ix_event_outbox_published ON event_outbox (published);

CREATE FUNCTION reject_evidence_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Surveillance evidence is immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER immutable_evidence
BEFORE UPDATE OR DELETE ON trading_events
FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();

CREATE TRIGGER immutable_evidence
BEFORE UPDATE OR DELETE ON alert_audit_log
FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();

COMMIT;
